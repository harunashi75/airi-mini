from datetime import datetime, timezone
import logging
import unicodedata
from typing import Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator
from core.i18n import t
from core.storage import ROOT, StorageError, StorageSyncError, atomic_write_json, project_path, read_json

logger = logging.getLogger(__name__)


def fold(text):
    """Lower-case and strip accents while keeping one character per input character."""
    return "".join((unicodedata.normalize("NFKD", char.lower()) or char)[0] for char in text)


def excerpt(text, start, length, radius=60):
    begin, end = max(0, start - radius), min(len(text), start + length + radius)
    return ("…" if begin else "") + " ".join(text[begin:end].split()) + ("…" if end < len(text) else "")


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=100000)
    # "command": shown to the user but never sent to the model nor used as a title.
    kind: Literal["chat", "command"] = "chat"
    # True when generation stopped early (client gone, error); content is what was produced.
    interrupted: bool = False


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str = Field(default_factory=lambda: t("session.default_title"))
    messages: list[Message] = Field(default_factory=list, max_length=10000)
    activity: str = Field(default="", max_length=500)
    mood: str = Field(default="", max_length=100)
    # Rolling summary of this conversation's older exchanges (never shared with other sessions).
    summary: str = Field(default="", max_length=8000)
    summary_upto: int = Field(default=0, ge=0)  # stored messages already covered by the summary
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def valid_turns(self):
        if len(self.messages) % 2 or any(m.role != ("user" if i % 2 == 0 else "assistant") for i, m in enumerate(self.messages)):
            raise ValueError(t("session.bad_turns"))
        return self


class SessionStore:
    def __init__(self, directory=None):
        self.directory = project_path(directory) if directory is not None else ROOT / "data" / "sessions"

    def _path(self, session_id):
        try:
            canonical = str(UUID(session_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(t("session.bad_id")) from exc
        return self.directory / f"{canonical}.json"

    def create(self):
        session = Session(id=str(uuid4()))
        self.save(session)
        return session

    def get(self, session_id):
        path = self._path(session_id)
        if not path.exists():
            raise KeyError(t("session.not_found"))
        session = Session.model_validate(read_json(path))
        if session.id != path.stem:
            raise ValueError(t("session.id_mismatch"))
        return session

    def save(self, session):
        updated_at = datetime.now(timezone.utc).isoformat()
        data = session.model_dump()
        data["updated_at"] = updated_at
        try:
            atomic_write_json(self._path(session.id), data)
        except StorageSyncError:
            session.updated_at = updated_at
            raise
        session.updated_at = updated_at

    def rename(self, session_id, title):
        title = " ".join(title.split())
        if not 1 <= len(title) <= 120:
            raise ValueError(t("session.title_length"))
        session = self.get(session_id)
        session.title = title
        self.save(session)
        return session

    def delete(self, session_id):
        """Remove the conversation and its backup; personal data must not linger after deletion."""
        path = self._path(session_id)
        if not path.exists():
            raise KeyError(t("session.not_found"))
        try:
            path.unlink()
            path.with_suffix(path.suffix + ".bak").unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(t("session.delete_failed", name=path.name)) from exc

    def search(self, query, limit=50):
        """Conversations whose title or messages contain `query` (case- and accent-insensitive)."""
        needle = fold(" ".join(query.split()))
        if len(needle) < 2:
            raise ValueError(t("search.too_short"))
        results = []
        for path in self.directory.glob("*.json") if self.directory.exists() else []:
            try:
                session = self.get(path.stem)
            except (StorageError, ValueError):
                continue
            snippet, matches = "", 0
            if needle in fold(session.title):
                matches += 1
            for message in session.messages:
                folded = fold(message.content)
                count = folded.count(needle)
                if count and not snippet:
                    snippet = excerpt(message.content, folded.index(needle), len(needle))
                matches += count
            if matches:
                results.append({"id": session.id, "title": session.title, "updated_at": session.updated_at,
                                "snippet": snippet, "matches": matches})
        results.sort(key=lambda item: item["updated_at"], reverse=True)
        return results[:limit]

    def list(self):
        sessions = []
        if self.directory.exists():
            for path in self.directory.glob("*.json"):
                try:
                    session = self.get(path.stem)
                    sessions.append({"id": session.id, "title": session.title, "updated_at": session.updated_at})
                except (StorageError, ValueError):
                    logger.warning("Conversation illisible conservée : %s", path.name)
                    sessions.append({"id": path.stem, "title": t("session.unreadable_title", id=path.stem[:8]), "updated_at": ""})
        return sorted(sessions, key=lambda item: item["updated_at"], reverse=True)
