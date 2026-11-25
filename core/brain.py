"""One serialized inference engine; persistent, isolated conversations."""
import json
import logging
from collections import OrderedDict
from threading import RLock
from core.identity import Identity
from core.personality import Personality
from core.ethics import ethics_prompt
from core.relationship import Relationship
from core.mood import Mood
from core.memory import MemoryManager
from core.recall import rank
from core.suggestions import MemorySuggestion, SuggestionFilter, suggestion_prompt
from core.i18n import set_language, t, value, all_values
from core.sessions import Message, SessionStore
from core.settings import load_settings
from core.storage import project_path, read_json

logger = logging.getLogger(__name__)
MEMORIES_PER_PAGE = 10
MEMORY_PAGE_BODY_LIMIT = 12_000
MESSAGE_OVERHEAD = 64  # chat-template tokens allowed per message
JOIN_MARGIN = 4  # "\n\n" separator plus possible token merges at the seam
TOKEN_CACHE_SIZE = 4096
SUMMARY_MESSAGE_CHARS = 1500



class Brain:
    def __init__(self, settings=None, llm=None, sessions=None, memory=None):
        self.settings = settings or load_settings()
        set_language(self.settings.language)
        self.lock = RLock()
        self.sessions = sessions or SessionStore()
        self.memory_manager = memory or MemoryManager()
        self.identity = Identity()
        self.personality = Personality()
        self.relationship = Relationship()
        self.mood = Mood()
        self.profile_path = project_path("data/user_profile.json")
        self.heritage_path = project_path("data/heritage_haru.json")
        self._load_context_files()
        if llm is None:
            from llama_cpp import Llama
            path = project_path(self.settings.model_path)
            if not path.is_file():
                raise FileNotFoundError(t("model.missing", path=path))
            llm = Llama(model_path=str(path), n_ctx=self.settings.n_ctx,
                        n_threads=self.settings.n_threads, n_batch=self.settings.n_batch,
                        n_gpu_layers=self.settings.n_gpu_layers, verbose=False)
        self.llm = llm
        self._cli_session = None
        self._token_cache = OrderedDict()

    def _load_context_files(self):
        profile, heritage = read_json(self.profile_path, {}), read_json(self.heritage_path, {})
        if not isinstance(profile, dict) or not isinstance(heritage, dict):
            raise ValueError(t("profile.not_object"))
        self.profile, self.heritage = profile, heritage

    @property
    def user_name(self):
        """How to name the user: profile "nom"/"name", else the identity's first form of address."""
        for key in ("nom", "name"):
            name = self.profile.get(key)
            if isinstance(name, str) and name.strip():
                return name.strip()
        addresses = self.identity.data.communication.address_user_as
        return addresses[0] if addresses else None

    @property
    def assistant_name(self):
        return self.identity.name

    def _user(self):
        return self.user_name or t("user.default_name")

    @property
    def profile_files(self):
        """Export name -> file actually used, so imports write where this brain reads."""
        return {"data/identity.json": self.identity.path, "data/mood.json": self.mood.path,
                "data/relationship.json": self.relationship.path, "data/user_profile.json": self.profile_path,
                "data/heritage_haru.json": self.heritage_path, "config/personality.json": self.personality.path}

    def reload_profile(self):
        """Re-read profile files after an import; keeps the current values if one is invalid."""
        with self.lock:
            identity, personality = Identity(self.identity.path), Personality(self.personality.path)
            relationship, mood = Relationship(self.relationship.path), Mood(self.mood.path)
            self._load_context_files()
            self.identity, self.personality, self.relationship, self.mood = identity, personality, relationship, mood
            self._token_cache.clear()

    def _get_system_prompt(self, session):
        parts = [ethics_prompt(self._user(), self.assistant_name), self.identity.prompt(), self.personality.prompt(),
                 "\n".join(self.relationship.prompt().splitlines()[:2]), self.mood.prompt()]
        if session.activity:
            parts.append(t("prompt.activity") + "\n" + session.activity)
        if session.mood:
            parts.append(t("prompt.session_mood") + "\n" + session.mood)
        if self.settings.suggest_memories:
            parts.append(suggestion_prompt(self._user()))
        return "\n\n".join(parts)

    def _tokens(self, text):
        """Token count of one text, memoized: prompts, memories and history repeat every turn."""
        count = self._token_cache.get(text)
        if count is None:
            count = len(self.llm.tokenize(text.encode("utf-8"), special=True))
            self._token_cache[text] = count
            if len(self._token_cache) > TOKEN_CACHE_SIZE:
                self._token_cache.popitem(last=False)
        else:
            self._token_cache.move_to_end(text)
        return count

    def _token_cost(self, messages):
        # Conservative template allowance; llama.cpp performs the final exact check.
        return 256 + sum(self._tokens(m["content"]) + MESSAGE_OVERHEAD for m in messages)

    def _summary_tokens(self):
        # Never let the summary eat more than a quarter of the usable context.
        return min(self.settings.summary_max_tokens, (self.settings.n_ctx - self.settings.max_tokens) // 4)

    @staticmethod
    def _chat_pairs(session, start=0):
        """(end index, user, assistant) for the exchanges the model may see, from stored index `start`."""
        stored = session.messages
        pairs = []
        for index in range(start - start % 2, len(stored) - 1, 2):
            question, answer = stored[index], stored[index + 1]
            if question.kind == "command" or answer.kind == "command":
                continue
            if answer.interrupted and not answer.content.strip():
                continue
            pairs.append((index + 2, question.content, answer.content))
        return pairs

    def _fit_pairs(self, pairs, budget, limit):
        """Most recent pairs whose total cost fits `budget`; linear thanks to cached counts."""
        kept, used = 0, 0
        for _, question, answer in reversed(pairs[-limit:] if limit else []):
            cost = self._tokens(question) + self._tokens(answer) + 2 * MESSAGE_OVERHEAD
            if used + cost > budget:
                break
            used += cost
            kept += 1
        return pairs[len(pairs) - kept:] if kept else []

    def _summarize(self, session, pairs):
        """Fold `pairs` into the session's rolling summary, in as many passes as the context needs."""
        limit = self._summary_tokens()
        budget = self.settings.n_ctx - limit
        batch = []
        instructions = t("summary.instructions", user=self._user(), assistant=self.assistant_name)
        speaker = self.user_name or t("user.default_name").capitalize()

        def flush():
            request = t("summary.request", previous=session.summary or t("summary.none"), transcript="\n".join(batch))
            reply = self.llm.create_chat_completion(
                messages=[{"role": "system", "content": instructions}, {"role": "user", "content": request}],
                temperature=0.2, max_tokens=limit, stream=False)
            text = (reply["choices"][0]["message"].get("content") or "").strip()
            if not text:
                raise RuntimeError("empty summary")
            session.summary = text[:8000]
            batch.clear()

        for end, question, answer in pairs:
            lines = [t("summary.line", speaker=speaker, text=question[:SUMMARY_MESSAGE_CHARS]),
                     t("summary.line", speaker=self.assistant_name, text=answer[:SUMMARY_MESSAGE_CHARS])]
            header = self._token_cost([{"content": instructions}, {"content": session.summary}])
            if batch and header + self._tokens("\n".join(batch + lines)) > budget:
                flush()
            batch.extend(lines)
            session.summary_upto = end
        if batch:
            flush()

    def _messages(self, session, user_text):
        system = {"role": "system", "content": self._get_system_prompt(session)}
        user = {"role": "user", "content": user_text}
        budget = self.settings.n_ctx - self.settings.max_tokens
        base_cost = self._token_cost([system, user])
        if base_cost > budget:
            raise ValueError(t("message.too_long_for_context"))
        # Optional context is bounded and never silently truncates the current user message.
        additions = [self.relationship.prompt(), t("prompt.profile") + "\n" + json.dumps(self.profile, ensure_ascii=False),
                     t("prompt.heritage") + "\n" + json.dumps(self.heritage, ensure_ascii=False)]
        # Most relevant memories first (with the previous question for follow-ups), then the newest.
        facts = self.memory_manager.long_term
        previous = next((q for _, q, _ in reversed(self._chat_pairs(session))), "")
        additions += [t("prompt.memory") + "\n" + facts[index] for index in rank(facts, user_text + "\n" + previous)]
        optional_budget = min(budget, base_cost + self.settings.n_ctx // 3)
        parts, cost, omitted = [system["content"]], base_cost, 0
        for addition in additions:
            # Joining texts may only merge tokens at the seam; JOIN_MARGIN keeps the sum an upper bound.
            extra = self._tokens(addition) + JOIN_MARGIN
            if cost + extra <= optional_budget:
                parts.append(addition)
                cost += extra
            else:
                omitted += 1
        if omitted:
            logger.info("Contexte optionnel partiel : %d blocs omis", omitted)

        summarize = self.settings.summarize_history
        reserve = self._summary_tokens() + MESSAGE_OVERHEAD if summarize else 0
        history_budget = budget - cost - reserve
        limit = self.settings.max_history_messages // 2
        pairs = self._chat_pairs(session, session.summary_upto if summarize else 0)
        kept = self._fit_pairs(pairs, history_budget, limit)
        if summarize and len(kept) < len(pairs):
            # Fold generously (keep half the room) so the next turns do not each need a summary pass.
            kept = self._fit_pairs(pairs, history_budget // 2, max(1, limit // 2))
            folded = pairs[:len(pairs) - len(kept)]
            backup = (session.summary, session.summary_upto)
            try:
                self._summarize(session, folded)
                self.sessions.save(session)
                logger.info("Résumé mis à jour (%d échanges repliés)", len(folded))
            except Exception:
                session.summary, session.summary_upto = backup
                logger.warning("Résumé impossible ; historique simplement tronqué", exc_info=True)
                kept = self._fit_pairs(pairs, history_budget, limit)
        if summarize and session.summary:
            block = t("prompt.summary") + "\n" + session.summary
            if cost + self._tokens(block) + JOIN_MARGIN <= budget:
                parts.append(block)
                cost += self._tokens(block) + JOIN_MARGIN
                # A summary written under other settings may exceed the reserve.
                kept = self._fit_pairs(kept, budget - cost, limit)
        system = {"role": "system", "content": "\n\n".join(parts)}
        history = []
        for _, question, answer in kept:
            history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        return [system, *history, user]

    def _list_memories(self, value):
        try:
            page = int(value or "1")
        except ValueError:
            page = 0
        if page < 1 or (value and not value.isdecimal()):
            raise ValueError(t("memories.bad_page"))

        facts = self.memory_manager.long_term
        if not facts:
            if page == 1:
                return t("memories.empty")
            raise ValueError(t("memories.empty"))

        pages = []
        for start in range(0, len(facts), MEMORIES_PER_PAGE):
            group = "\n".join(f"{number}. {fact}" for number, fact in
                              enumerate(facts[start:start + MEMORIES_PER_PAGE], start + 1))
            # Older JSON files can contain facts longer than the current input limit.
            pages.extend(group[offset:offset + MEMORY_PAGE_BODY_LIMIT]
                         for offset in range(0, len(group), MEMORY_PAGE_BODY_LIMIT))

        if page > len(pages):
            raise ValueError(t("memories.no_page", count=len(pages)))
        if len(pages) == 1:
            return t("memories.title") + "\n" + pages[0] + "\n\n" + t("memories.forget_hint")

        response = t("memories.title_paged", page=page, count=len(pages)) + "\n" + pages[page - 1]
        if page < len(pages):
            return response + "\n\n" + t("memories.next", page=page + 1)
        return response + "\n\n" + t("memories.forget_hint")

    @staticmethod
    def _as_command(text):
        """The slash command `text` stands for, or None for an ordinary message."""
        head, colon, rest = text.partition(":")
        if colon:
            for name, phrase in value("PHRASES").items():
                if head.strip().casefold() == phrase:
                    return value("COMMANDS")[name] + " " + rest.strip()
        return text if text.startswith("/") else None

    def _command(self, session, text):
        text = self._as_command(text)
        if text is None:
            return None
        word, _, argument = text.partition(" ")
        word, argument = word.casefold(), argument.strip()
        command = next((name for name, typed in value("COMMANDS").items() if typed == word), None)
        if command == "help":
            return t("help", assistant=self.assistant_name)
        if command == "memories":
            return self._list_memories(argument)
        if command == "remember":
            self.memory_manager.add_fact(argument)
            return t("remember.done", fact=argument)
        if command == "forget":
            if not argument:
                raise ValueError(t("forget.missing_value"))
            if argument.isdecimal():
                removed = self.memory_manager.remove_at(int(argument))
                return t("forget.done_number", number=argument, fact=removed)
            return t("forget.done") if self.memory_manager.remove_fact(argument) else t("forget.not_found")
        if command in ("activity", "mood"):
            limit = 500 if command == "activity" else 100
            if len(argument) > limit:
                raise ValueError(t("setting.too_long", limit=limit))
            setattr(session, command, argument)
            return t(f"{command}.done")
        if command in ("familiarity", "topic", "moment"):
            updated = self.relationship.data.copy()
            if command == "familiarity":
                try:
                    number = float(argument.replace(",", "."))
                except ValueError as exc:
                    raise ValueError(t("familiarity.not_number")) from exc
                if not 0 <= number <= 1:
                    raise ValueError(t("familiarity.range"))
                updated["familiarity"] = number
            else:
                if not argument or len(argument) > 500:
                    raise ValueError(t("relationship.bad_text"))
                key = "recurring_topics" if command == "topic" else "important_moments"
                values = list(updated.get(key, []))
                if argument not in values:
                    if len(values) >= 50:
                        raise ValueError(t("relationship.full"))
                    values.append(argument)
                updated[key] = values
            self.relationship.update(**updated)
            return t("relationship.done")
        raise ValueError(t("command.unknown"))

    def stream(self, user_text, session_id, replace_last=False):
        """Answer `user_text`. With replace_last, it replaces the last exchange (regenerate or edit):
        the old exchange stays on disk until the new one is saved."""
        user_text = user_text.strip()
        if not user_text or len(user_text) > self.settings.max_message_chars:
            raise ValueError(t("message.length", limit=self.settings.max_message_chars))
        # Also guards tokenizer, memory edits, session state, and inference across clients.
        with self.lock:
            session = self.sessions.get(session_id)
            if replace_last:
                if self._as_command(user_text) is not None:
                    raise ValueError(t("message.edit_is_command"))
                if len(session.messages) < 2 or session.messages[-1].kind == "command":
                    raise ValueError(t("message.nothing_to_replace", assistant=self.assistant_name))
                session.messages = session.messages[:-2]
                session.summary_upto = min(session.summary_upto, len(session.messages))
            response = self._command(session, user_text)
            if response is not None:
                # Commands are shown in the conversation but kept out of the model's context.
                self._save_exchange(session, user_text, response, kind="command")
                yield response
                return
            chunks = self.llm.create_chat_completion(
                messages=self._messages(session, user_text), temperature=self.settings.temperature,
                max_tokens=self.settings.max_tokens, stream=True)
            pieces = []
            completed = disconnected = False
            marker = SuggestionFilter()
            try:
                for chunk in chunks:
                    delta = marker.feed(chunk["choices"][0].get("delta", {}).get("content") or "")
                    if delta:
                        pieces.append(delta)
                        yield delta
                tail, fact = marker.finish()
                if tail:
                    pieces.append(tail)
                    yield tail
                completed = True
            except GeneratorExit:
                disconnected = True
                raise
            finally:
                close = getattr(chunks, "close", None)
                if close:
                    close()
                if not completed and (pieces or disconnected):
                    self._save_interrupted(session, user_text, "".join(pieces))
            response = "".join(pieces).rstrip()
            if not response:
                raise RuntimeError(t("model.empty_reply"))
            self._save_exchange(session, user_text, response)
            if fact and self.settings.suggest_memories and not self.memory_manager.contains(fact):
                # Only a proposal: nothing is stored until the user confirms it.
                yield MemorySuggestion(fact)

    def _save_exchange(self, session, user_text, response, kind="chat", interrupted=False):
        session.messages.extend([Message(role="user", content=user_text, kind=kind),
                                 Message(role="assistant", content=response, kind=kind, interrupted=interrupted)])
        before = len(session.messages)
        session.messages = session.messages[-self.settings.max_saved_messages:]
        session.summary_upto = max(0, session.summary_upto - (before - len(session.messages)))
        if session.title in all_values("session.default_title") and kind == "chat":
            session.title = user_text[:60]
        self.sessions.save(session)
        logger.info("Échange sauvegardé (%d messages%s)", len(session.messages), ", interrompu" if interrupted else "")

    def _save_interrupted(self, session, user_text, partial):
        """Keep the user's message and whatever was generated when the stream stops early."""
        try:
            self._save_exchange(session, user_text, partial, interrupted=True)
        except Exception:
            # Never mask the original interruption or error.
            logger.exception("Impossible de sauvegarder l'échange interrompu")

    def think(self, user_text, session_id=None, replace_last=False):
        if session_id is None:
            with self.lock:
                if self._cli_session is None:
                    self._cli_session = self.sessions.create().id
                session_id = self._cli_session
        return "".join(part for part in self.stream(user_text, session_id, replace_last) if isinstance(part, str))

    def close(self):
        close = getattr(self.llm, "close", None)
        if close:
            close()
