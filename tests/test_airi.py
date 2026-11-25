import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import socket
from stat import S_ISDIR
import subprocess
import sys
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from core.backup import export_bundle, import_bundle, preview_bundle
from core.brain import Brain
from core.memory import MemoryManager
from core.recall import rank
from core.suggestions import MemorySuggestion, SuggestionFilter
from core.sessions import SessionStore
from core.settings import Settings
from core.storage import ROOT, DataDirectoryBusy, StorageError, StorageSyncError, atomic_write_json, lock_data_directory, read_json


@contextmanager
def fail_directory_sync():
    sync_file = os.fsync

    def sync(fd):
        if S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('échec simulé de synchronisation du dossier')
        sync_file(fd)

    with patch('core.storage.os.fsync', side_effect=sync):
        yield


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.active = 0
        self.max_active = 0
        self.delay = 0
        self.entered = threading.Event()
        self.summary_calls = []
        self.summary_fail = False
        self.reply = None
        self.piece_delay = 0
        self.tokenized = 0

    def tokenize(self, text, **kwargs):
        self.tokenized += 1
        return list(text)  # Byte count deliberately overestimates real token count.

    def create_chat_completion(self, **kwargs):
        if not kwargs.get('stream'):
            self.summary_calls.append(kwargs)
            if self.summary_fail:
                raise RuntimeError('summary failure')
            return {"choices": [{"message": {"content": f"Résumé n°{len(self.summary_calls)}"}}]}
        return self._stream(kwargs)

    def _stream(self, kwargs):
        self.calls.append(kwargs)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.reply:
                for piece in self.reply:
                    time.sleep(self.piece_delay)
                    yield {"choices": [{"delta": {"content": piece}}]}
                return
            yield {"choices": [{"delta": {"content": "Bonjour "}}]}
            if self.fail:
                raise RuntimeError("test model failure")
            yield {"choices": [{"delta": {"content": "Haru !"}}]}
        finally:
            self.active -= 1

    def close(self):
        pass


class BrainFixture:

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # Tests never read or write the real data/: they start from the public examples.
        shutil.copytree(ROOT / 'data.example', self.root / 'data')
        shutil.copytree(ROOT / 'config', self.root / 'config')
        root_patch = patch('core.storage.ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.llm = FakeLLM()
        self.brain = Brain(settings=Settings(n_ctx=16384), llm=self.llm,
                           sessions=SessionStore(self.root / 'sessions'),
                           memory=MemoryManager(self.root / 'memory.json'))
        self.brain.relationship.path = self.root / 'relationship.json'
        self.a = self.brain.sessions.create().id
        self.b = self.brain.sessions.create().id


class BrainTests(BrainFixture, unittest.TestCase):
    def test_sessions_are_isolated_and_persistent(self):
        self.brain.think('secret A', self.a)
        self.brain.think('question B', self.b)
        messages = self.llm.calls[-1]['messages']
        self.assertFalse(any(m['content'] == 'secret A' for m in messages))
        restored = SessionStore(self.root / 'sessions').get(self.a)
        self.assertEqual([m.content for m in restored.messages], ['secret A', 'Bonjour Haru !'])

    def test_explicit_memory_and_commands(self):
        self.brain.think('/retiens Je construis AIRI.', self.a)
        self.assertEqual(MemoryManager(self.root / 'memory.json').long_term, ['Je construis AIRI.'])
        self.assertIn('Je construis AIRI.', self.brain.think('/souvenirs', self.b))
        self.brain.think('/oublie Je construis AIRI.', self.b)
        self.assertEqual(self.brain.memory_manager.long_term, [])
        self.brain.think('/humeur joyeuse', self.a)
        self.assertEqual(self.brain.sessions.get(self.a).mood, 'joyeuse')
        self.assertEqual(self.brain.sessions.get(self.b).mood, '')
        self.brain.think('/activite développement', self.a)
        self.brain.think('/familiarite 0,7', self.a)
        self.brain.think('/sujet Python', self.a)
        self.brain.think('/moment Première version', self.a)
        self.assertEqual(read_json(self.root / 'relationship.json')['familiarity'], .7)
        self.assertFalse(self.llm.calls)

    def test_memory_pages_validate_page_numbers(self):
        self.assertEqual(self.brain.think('/souvenirs', self.a), 'Aucun souvenir enregistré.')
        with self.assertRaisesRegex(ValueError, 'Aucun souvenir enregistré'):
            self.brain.think('/souvenirs 2', self.a)
        self.brain.memory_manager.add_fact('Premier souvenir')
        for number in ('0', '-1', '1.5', '1 2', 'texte'):
            with self.subTest(number=number), self.assertRaisesRegex(ValueError, 'numéro de page positif'):
                self.brain.think(f'/souvenirs {number}', self.a)
        with self.assertRaisesRegex(ValueError, 'Page inexistante'):
            self.brain.think('/souvenirs 2', self.a)
        self.assertEqual(self.brain.think('/souvenirs 1', self.a), 'Souvenirs enregistrés :\n1. Premier souvenir'
                         '\n\nTape /oublie suivi du numéro pour supprimer un souvenir.')

    def test_memory_pages_keep_oversized_legacy_fact(self):
        fact = 'é' * 100_123
        atomic_write_json(self.brain.memory_manager.path, {'long_term': [fact]})
        self.brain.memory_manager.load()
        page = 1
        parts = []
        while True:
            response = self.brain.think(f'/souvenirs {page}', self.a)
            lines = response.splitlines()
            parts.append(lines[1])
            if 'Tape /souvenirs' not in response:
                break
            page += 1
            self.assertLess(page, 20)
        self.assertEqual(''.join(parts), '1. ' + fact)
        self.assertEqual(self.brain.sessions.get(self.a).messages[-1].content, response)

    def test_memories_command_saves_before_display(self):
        self.brain.memory_manager.add_fact('Premier souvenir')
        with patch.object(self.brain.sessions, 'save', side_effect=StorageError('disque plein')):
            with self.assertRaisesRegex(StorageError, 'disque plein'):
                next(self.brain.stream('/souvenirs', self.a))
        self.assertEqual(self.brain.sessions.get(self.a).messages, [])

        with closing(self.brain.stream('/souvenirs', self.a)) as stream:
            response = next(stream)
            self.assertEqual(self.brain.sessions.get(self.a).messages[-1].content, response)

    def test_failed_generation_keeps_partial_answer(self):
        self.llm.fail = True
        with self.assertRaises(RuntimeError):
            self.brain.think('test', self.a)
        user, answer = self.brain.sessions.get(self.a).messages
        self.assertEqual((user.content, answer.content, answer.interrupted), ('test', 'Bonjour ', True))
        self.assertEqual(self.llm.active, 0)

    def test_failure_before_any_token_is_not_saved(self):
        with patch.object(self.llm, 'create_chat_completion', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.brain.think('test', self.a)
        self.assertEqual(self.brain.sessions.get(self.a).messages, [])

    def test_cancelled_stream_saves_partial_answer(self):
        stream = self.brain.stream('test', self.a)
        self.assertEqual(next(stream), 'Bonjour ')
        stream.close()
        session = self.brain.sessions.get(self.a)
        self.assertEqual([(m.role, m.content, m.interrupted) for m in session.messages],
                         [('user', 'test', False), ('assistant', 'Bonjour ', True)])
        self.assertEqual(session.title, 'test')
        self.assertEqual(self.llm.active, 0)
        # The partial answer stays in the model's context; the next exchange is complete.
        self.brain.think('suite', self.a)
        self.assertEqual([m['content'] for m in self.llm.calls[-1]['messages'][1:]], ['test', 'Bonjour ', 'suite'])

    def test_commands_are_shown_but_not_sent_to_model(self):
        self.brain.think('/aide', self.a)
        self.brain.think('/retiens Haru aime le thé.', self.a)
        session = self.brain.sessions.get(self.a)
        self.assertEqual(session.title, 'Nouvelle conversation')
        self.assertEqual([m.kind for m in session.messages], ['command'] * 4)
        self.brain.think('bonjour', self.a)
        sent = self.llm.calls[-1]['messages']
        self.assertEqual([m['role'] for m in sent], ['system', 'user'])
        self.assertEqual(sent[-1]['content'], 'bonjour')
        session = self.brain.sessions.get(self.a)
        self.assertEqual(session.title, 'bonjour')
        self.assertEqual(len(session.messages), 6)

    def test_context_budget_and_complete_pairs(self):
        self.brain.settings.n_ctx = 4096
        # Shorten the baseline for this controlled token-budget test.
        self.brain._get_system_prompt = lambda session: 'Instructions'
        self.brain.profile = {}; self.brain.heritage = {}
        for i in range(12):
            self.brain.think(str(i) + 'x' * 200, self.a)
        messages = self.llm.calls[-1]['messages']
        self.assertLessEqual(self.brain._token_cost(messages) + self.brain.settings.max_tokens, 4096)
        self.assertEqual([m['role'] for m in messages[1:-1]], ['user', 'assistant'] * ((len(messages) - 2) // 2))
        with self.assertRaisesRegex(ValueError, 'trop long'):
            self.brain.think('x' * 4000, self.a)

    def _small_context(self):
        self.brain.settings.n_ctx = 4096
        self.brain._get_system_prompt = lambda session: 'Instructions'
        self.brain.profile = {}; self.brain.heritage = {}

    def test_overflowing_history_is_folded_into_summary(self):
        self._small_context()
        for i in range(12):
            self.brain.think(f'{i:02d}' + 'x' * 200, self.a)
        session = self.brain.sessions.get(self.a)
        self.assertTrue(self.llm.summary_calls)
        self.assertTrue(session.summary.startswith('Résumé n°'))
        self.assertGreater(session.summary_upto, 0)
        sent = self.llm.calls[-1]['messages']
        self.assertIn('## RÉSUMÉ DES ÉCHANGES PRÉCÉDENTS', sent[0]['content'])
        self.assertIn(session.summary, sent[0]['content'])
        # Folded exchanges are no longer sent verbatim; the newest ones still are.
        contents = [m['content'] for m in sent[1:]]
        self.assertNotIn('00' + 'x' * 200, contents)
        self.assertIn('10' + 'x' * 200, contents)
        self.assertLessEqual(self.brain._token_cost(sent) + self.brain.settings.max_tokens, 4096)
        # Folding is generous, so a summary pass is not needed on every turn.
        self.assertLess(len(self.llm.summary_calls), 6)
        transcript = self.llm.summary_calls[0]['messages'][1]['content']
        self.assertIn(f'{self.brain.user_name} : 00', transcript)
        self.assertIn(f'{self.brain.assistant_name} : Bonjour Haru !', transcript)

    def test_summary_failure_falls_back_to_truncation(self):
        self._small_context()
        self.llm.summary_fail = True
        for i in range(12):
            self.brain.think(f'{i:02d}' + 'x' * 200, self.a)
        session = self.brain.sessions.get(self.a)
        self.assertEqual((session.summary, session.summary_upto), ('', 0))
        sent = self.llm.calls[-1]['messages']
        self.assertLessEqual(self.brain._token_cost(sent) + self.brain.settings.max_tokens, 4096)
        self.assertEqual([m['role'] for m in sent[1:-1]], ['user', 'assistant'] * ((len(sent) - 2) // 2))

    def test_summary_can_be_disabled(self):
        self._small_context()
        self.brain.settings.summarize_history = False
        for i in range(12):
            self.brain.think(f'{i:02d}' + 'x' * 200, self.a)
        self.assertFalse(self.llm.summary_calls)
        self.assertNotIn('RÉSUMÉ', self.llm.calls[-1]['messages'][0]['content'])

    def test_summary_index_follows_trimmed_history(self):
        self.brain.settings.max_saved_messages = 6
        session = self.brain.sessions.get(self.a)
        for i in range(3):
            self.brain.think(f'q{i}', self.a)
        session = self.brain.sessions.get(self.a)
        session.summary, session.summary_upto = 'Résumé', 4
        self.brain.sessions.save(session)
        self.brain.think('q3', self.a)
        session = self.brain.sessions.get(self.a)
        self.assertEqual([m.content for m in session.messages[::2]], ['q1', 'q2', 'q3'])
        self.assertEqual(session.summary_upto, 2)

    def test_token_counts_are_cached(self):
        self._small_context()
        for i in range(6):
            self.brain.think(f'{i:02d}' + 'x' * 200, self.a)
        before = self.llm.tokenized
        self.brain.think('encore', self.a)
        # Only the new user message needs tokenizing; history and prompt counts are reused.
        self.assertLessEqual(self.llm.tokenized - before, 2)

    def test_forget_by_number(self):
        for fact in ('Premier', 'Deuxième', 'Troisième'):
            self.brain.memory_manager.add_fact(fact)
        listing = self.brain.think('/souvenirs', self.a)
        self.assertIn('2. Deuxième', listing)
        self.assertIn('Deuxième', self.brain.think('/oublie 2', self.a))
        self.assertEqual(self.brain.memory_manager.long_term, ['Premier', 'Troisième'])
        for bad in ('0', '3'):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'entre 1 et 2'):
                self.brain.think(f'/oublie {bad}', self.a)
        self.assertEqual(self.brain.think('/oublie Premier', self.a), 'Souvenir supprimé.')
        self.assertEqual(self.brain.memory_manager.long_term, ['Troisième'])

    def test_relevant_old_memories_come_first(self):
        old = 'Haru joue du violoncelle depuis dix ans.'
        self.brain.memory_manager.add_fact(old)
        for i in range(30):
            self.brain.memory_manager.add_fact(f'Fait récent numéro {i} sur la cuisine.')
        self.brain.think('Tu te souviens de mes violoncelles ?', self.a)
        system = self.llm.calls[-1]['messages'][0]['content']
        memories = [line for line in system.split('## SOUVENIR EXPLICITE\n')[1:]]
        self.assertTrue(memories[0].startswith(old))
        self.assertTrue(memories[1].startswith('Fait récent numéro 29'))

    def test_rank_handles_accents_plurals_and_no_match(self):
        facts = ['Haru aime le café.', 'Haru habite à Montréal.', 'Haru adore les échecs.']
        self.assertEqual(rank(facts, 'Quel CAFE tu me conseilles ?')[0], 0)
        self.assertEqual(rank(facts, 'une partie d\'echec ?')[0], 2)
        self.assertEqual(rank(facts, 'bonjour'), [2, 1, 0])
        self.assertEqual(rank([], 'café'), [])

    def test_memory_suggestion_is_proposed_not_saved(self):
        self.llm.reply = ['J\'adore ', 'ça !\n[[ret', 'enir: Haru aime ', 'le jazz]]']
        parts = list(self.brain.stream('J\'aime le jazz', self.a))
        self.assertEqual(parts[-1], MemorySuggestion('Haru aime le jazz'))
        self.assertEqual(''.join(p for p in parts if isinstance(p, str)).rstrip(), 'J\'adore ça !')
        self.assertEqual(self.brain.memory_manager.long_term, [])
        self.assertEqual(self.brain.sessions.get(self.a).messages[-1].content, 'J\'adore ça !')
        self.assertIn('[[retenir:', self.llm.calls[-1]['messages'][0]['content'])
        # Already known facts and disabled suggestions produce no proposal.
        self.brain.memory_manager.add_fact('haru aime le  JAZZ')
        self.assertFalse(any(isinstance(p, MemorySuggestion) for p in self.brain.stream('Encore', self.a)))
        self.brain.settings.suggest_memories = False
        self.brain.memory_manager.clear()
        self.assertFalse(any(isinstance(p, MemorySuggestion) for p in self.brain.stream('Encore', self.a)))
        self.assertNotIn('[[retenir:', self.llm.calls[-1]['messages'][0]['content'])

    def test_suggestion_filter_edge_cases(self):
        def run(chunks):
            f = SuggestionFilter()
            visible = ''.join(f.feed(c) for c in chunks)
            tail, fact = f.finish()
            return visible + tail, fact
        self.assertEqual(run(['a [', '[ pas un marqueur']), ('a [[ pas un marqueur', None))
        self.assertEqual(run(['fin [[reten']), ('fin [[reten', None))
        self.assertEqual(run(['x[[retenir: sans fin']), ('x', 'sans fin'))
        self.assertEqual(run(['x[[retenir: ]]']), ('x', None))
        self.assertEqual(run(['x[[retenir: ' + 'a' * 400 + ']]']), ('x', None))

    def test_regenerate_and_edit_replace_last_exchange(self):
        self.brain.think('première', self.a)
        self.brain.think('deuxième', self.a)
        self.llm.reply = ['Autre réponse']
        self.brain.think('deuxième', self.a, replace_last=True)
        self.brain.think('deuxième corrigée', self.a, replace_last=True)
        session = self.brain.sessions.get(self.a)
        self.assertEqual([m.content for m in session.messages],
                         ['première', 'Bonjour Haru !', 'deuxième corrigée', 'Autre réponse'])
        # The replaced exchange is not sent back to the model.
        self.assertEqual([m['content'] for m in self.llm.calls[-1]['messages'][1:]],
                         ['première', 'Bonjour Haru !', 'deuxième corrigée'])

    def test_replace_last_keeps_old_exchange_on_failure_and_refuses_commands(self):
        self.brain.think('question', self.a)
        with patch.object(self.llm, 'create_chat_completion', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.brain.think('question', self.a, replace_last=True)
        self.assertEqual([m.content for m in self.brain.sessions.get(self.a).messages], ['question', 'Bonjour Haru !'])
        with self.assertRaisesRegex(ValueError, 'commande'):
            self.brain.think('/aide', self.a, replace_last=True)
        self.brain.think('/aide', self.a)
        with self.assertRaisesRegex(ValueError, 'Aucun échange à remplacer'):
            self.brain.think('question', self.a, replace_last=True)
        with self.assertRaisesRegex(ValueError, 'Aucun échange à remplacer'):
            self.brain.think('question', self.b, replace_last=True)

    def test_rename_delete_and_search_sessions(self):
        store = self.brain.sessions
        self.brain.think('Parlons de JAZZ et de piano', self.a)
        self.brain.think('Autre sujet', self.b)
        self.assertEqual(store.rename(self.b, '  Mon   titre ').title, 'Mon titre')
        self.assertEqual(store.get(self.b).title, 'Mon titre')
        with self.assertRaisesRegex(ValueError, 'entre 1 et 120'):
            store.rename(self.b, '   ')
        results = store.search('jazz')
        self.assertEqual([r['id'] for r in results], [self.a])
        self.assertIn('JAZZ', results[0]['snippet'])
        self.assertEqual([r['id'] for r in store.search('ÉLÈVE')], [])
        self.brain.think('Une élève douée', self.b)
        self.assertEqual([r['id'] for r in store.search('eleve')], [self.b])
        self.assertEqual([r['id'] for r in store.search('mon titre')], [self.b])
        with self.assertRaisesRegex(ValueError, 'deux caractères'):
            store.search('a')
        store.save(store.get(self.a))  # creates a .bak
        store.delete(self.a)
        self.assertFalse(list((self.root / 'sessions').glob(self.a + '*')))
        with self.assertRaises(KeyError):
            store.delete(self.a)
        self.assertEqual([s['id'] for s in store.list()], [self.b])

    def _profile_in_tmp(self):
        """Point every profile file at a temporary copy so imports never touch the real data/."""
        for name, path in self.brain.profile_files.items():
            target = self.root / 'profile' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes()) if path.exists() else target.write_text('{}')
        self.brain.identity.path = self.root / 'profile/data/identity.json'
        self.brain.mood.path = self.root / 'profile/data/mood.json'
        self.brain.relationship.path = self.root / 'profile/data/relationship.json'
        self.brain.personality.path = self.root / 'profile/config/personality.json'
        self.brain.profile_path = self.root / 'profile/data/user_profile.json'
        self.brain.heritage_path = self.root / 'profile/data/heritage_haru.json'

    def test_export_import_round_trip(self):
        self._profile_in_tmp()
        self.brain.think('souvenir de conversation', self.a)
        self.brain.memory_manager.add_fact('Fait A')
        bundle = json.loads(json.dumps(export_bundle(self.brain)))
        self.assertEqual(bundle['format'], 'airi-mini-export')
        self.assertEqual(len(bundle['sessions']), 2)
        self.assertEqual(set(bundle['profile']), set(self.brain.profile_files))

        # Import into a fresh brain: everything comes back.
        other = Brain(settings=Settings(n_ctx=16384), llm=FakeLLM(), sessions=SessionStore(self.root / 'other'),
                      memory=MemoryManager(self.root / 'other-memory.json'))
        other.memory_manager.add_fact('Fait B')
        for name in ('identity', 'mood', 'relationship', 'personality'):
            getattr(other, name).path = self.root / f'other-{name}.json'
        other.profile_path, other.heritage_path = self.root / 'other-profile.json', self.root / 'other-heritage.json'
        preview = preview_bundle(other, bundle)
        self.assertEqual((preview['sessions_new'], preview['sessions_existing'], preview['facts_new']), (2, 0, 1))
        report = import_bundle(other, bundle)
        self.assertEqual((report['sessions_added'], report['facts_added'], report['profile_files']), (2, 1, []))
        self.assertFalse((self.root / 'other-identity.json').exists())
        self.assertEqual(other.memory_manager.long_term, ['Fait B', 'Fait A'])
        self.assertEqual([m.content for m in other.sessions.get(self.a).messages][0], 'souvenir de conversation')
        # A second import adds nothing and never overwrites existing conversations.
        other.sessions.rename(self.a, 'Renommé ici')
        again = import_bundle(other, bundle)
        self.assertEqual((again['sessions_added'], again['sessions_skipped'], again['facts_added']), (0, 2, 0))
        self.assertEqual(other.sessions.get(self.a).title, 'Renommé ici')
        # The profile is restored only on request, then reloaded.
        bundle['profile']['data/identity.json']['name'] = 'AIRI importée'
        report = import_bundle(other, bundle, include_profile=True)
        self.assertEqual(len(report['profile_files']), 6)
        self.assertEqual(other.identity.name, 'AIRI importée')
        self.assertEqual(read_json(self.root / 'other-identity.json')['name'], 'AIRI importée')

    def test_import_validates_everything_before_writing(self):
        self._profile_in_tmp()
        bundle = export_bundle(self.brain)
        for broken, message in [
            ({**bundle, 'format': 'autre'}, "pas un export"),
            ({**bundle, 'version': 99}, 'Version'),
            ({**bundle, 'sessions': [{'id': 'x', 'messages': [{'role': 'user', 'content': 'seul'}]}]}, 'mal formés'),
            ({**bundle, 'profile': {'data/mood.json': {'energy': 7}}}, 'mood.json'),
            ({**bundle, 'profile': {'../../etc/passwd': {}}}, 'inattendu'),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                import_bundle(self.brain, broken, include_profile=True)
        self.assertEqual(len(self.brain.sessions.list()), 2)

    def test_user_name_comes_from_profile(self):
        self.brain.profile = {'nom': 'Camille'}
        prompt = self.brain._get_system_prompt(self.brain.sessions.get(self.a))
        self.assertIn('bien-être de Camille', prompt)
        self.assertIn('Si Camille partage', prompt)
        self.assertNotIn('Haru', prompt.split('## PRINCIPES')[1].split('\n\n')[0])
        self.brain.profile = {'name': 'Sam'}
        self.assertEqual(self.brain.user_name, 'Sam')
        # Without a name in the profile, the identity's form of address, then a neutral word.
        self.brain.profile = {}
        addresses = self.brain.identity.data.communication.address_user_as
        self.assertEqual(self.brain.user_name, addresses[0] if addresses else None)
        self.brain.identity.data = self.brain.identity.data.model_copy(
            update={'communication': self.brain.identity.data.communication.model_copy(update={'address_user_as': []})})
        self.assertIsNone(self.brain.user_name)
        prompt = self.brain._get_system_prompt(self.brain.sessions.get(self.a))
        self.assertIn("bien-être de l'utilisateur", prompt)

    def test_no_hardcoded_user_name_in_code(self):
        for path in [*Path(ROOT, 'core').rglob('*.py'), Path(ROOT, 'app.py'), Path(ROOT, 'cli.py')]:
            with self.subTest(path=path.name):
                self.assertNotIn('Haru', path.read_text(encoding='utf-8').replace('heritage_haru', ''))

    def test_parallel_clients_are_serialized(self):
        self.llm.delay = .03
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.brain.think, 'test', sid) for sid in (self.a, self.b)]
            for future in futures:
                self.assertEqual(future.result(), 'Bonjour Haru !')
        self.assertEqual(self.llm.max_active, 1)

    def test_invalid_session_paths_and_messages(self):
        with self.assertRaises(ValueError):
            self.brain.sessions.get('../../identity')
        with self.assertRaises(ValueError):
            self.brain.think('   ', self.a)
        with self.assertRaises(ValueError):
            self.brain.think('/familiarite nan', self.a)
        with self.assertRaises(ValueError):
            self.brain.think('/retiens ' + 'x' * 1001, self.a)

    def test_atomic_backup_and_corruption_protection(self):
        path = self.root / 'test.json'
        atomic_write_json(path, {'old': True})
        atomic_write_json(path, {'new': True})
        self.assertEqual(read_json(path.with_suffix('.json.bak')), {'old': True})
        path.write_text('{corrompu')
        with self.assertRaises(StorageError):
            MemoryManager(path)
        with self.assertRaises(StorageError):
            atomic_write_json(path, {})
        self.assertEqual(path.read_text(), '{corrompu')

    def test_corrupt_session_does_not_hide_healthy_sessions(self):
        path = self.root / 'sessions' / (self.a + '.json')
        path.write_text('{corrompu')
        sessions = self.brain.sessions.list()
        self.assertEqual(len(sessions), 2)
        self.assertTrue(any(item['id'] == self.b for item in sessions))
        self.assertTrue(any('illisible' in item['title'] for item in sessions))
        with self.assertRaises(StorageError):
            self.brain.sessions.get(self.a)


    def test_write_failure_keeps_previous_memory(self):
        self.brain.memory_manager.add_fact('ancien')
        with patch('core.storage.os.fsync', side_effect=OSError('échec simulé de synchronisation du fichier')):
            with self.assertRaisesRegex(StorageError, 'Sauvegarde impossible'):
                self.brain.memory_manager.add_fact('nouveau')
        self.assertEqual(self.brain.memory_manager.long_term, ['ancien'])
        self.assertEqual(MemoryManager(self.root / 'memory.json').long_term, ['ancien'])

    @unittest.skipIf(os.name == 'nt', 'Windows ne synchronise pas le dossier dans ce chemin')
    def test_directory_sync_failure_keeps_memory_consistent(self):
        memory = self.brain.memory_manager
        memory.add_fact('ancien')
        relationship = self.brain.relationship
        relationship.set_familiarity(0.25)

        with fail_directory_sync():
            with self.assertRaisesRegex(StorageSyncError, 'a été mis à jour'):
                memory.add_fact('nouveau')
            with self.assertRaises(StorageSyncError):
                relationship.set_familiarity(0.75)

        self.assertEqual(memory.long_term, ['ancien', 'nouveau'])
        self.assertEqual(read_json(memory.path)['long_term'], memory.long_term)
        self.assertEqual(read_json(memory.path.with_suffix('.json.bak'))['long_term'], ['ancien'])
        self.assertEqual(relationship.familiarity, 0.75)
        self.assertEqual(read_json(relationship.path), relationship.data)

        memory.add_fact('suivant')
        self.assertEqual(MemoryManager(memory.path).long_term, ['ancien', 'nouveau', 'suivant'])

    @unittest.skipIf(os.name == 'nt', 'Windows ne synchronise pas le dossier dans ce chemin')
    def test_session_timestamp_tracks_the_replacement(self):
        sessions = self.brain.sessions
        session = sessions.get(self.a)
        previous_time = session.updated_at
        session.title = 'Titre modifié'

        with patch('core.storage.os.fsync', side_effect=OSError('échec simulé de synchronisation du fichier')):
            with self.assertRaisesRegex(StorageError, 'Sauvegarde impossible'):
                sessions.save(session)
        self.assertEqual(session.updated_at, previous_time)
        self.assertNotEqual(sessions.get(self.a).title, session.title)

        with fail_directory_sync(), self.assertRaises(StorageSyncError):
            sessions.save(session)
        saved = sessions.get(self.a)
        self.assertEqual(saved.title, session.title)
        self.assertEqual(saved.updated_at, session.updated_at)
        self.assertNotEqual(saved.updated_at, previous_time)

    def test_history_retention(self):
        self.brain.settings.max_saved_messages = 4
        for i in range(4):
            self.brain.think('/aide', self.a)
        self.assertEqual(len(self.brain.sessions.get(self.a).messages), 4)

    def test_paths_are_independent_of_working_directory(self):
        import os
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            from core.settings import load_settings
            self.assertEqual(load_settings().port, 8000)
            from core.identity import Identity
            self.assertTrue(Identity().name)
        finally:
            os.chdir(previous)


class DataDirectoryLockTests(unittest.TestCase):
    def test_busy_directory_blocks_writers_and_preserves_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            memory = home / "data" / "memory.json"
            atomic_write_json(memory, {"long_term": ["à conserver"]})
            atomic_write_json(home / "config" / "airi.json", {"open_browser": False})
            env = {
                **os.environ, "AIRIMINI_HOME": temp, "AIRIMINI_ASSETS": str(ROOT),
                "AIRIMINI_DESKTOP_TOKEN": "test", "PYTHONIOENCODING": "utf-8",
            }

            def launch(*args):
                return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True,
                                      encoding="utf-8", timeout=10)

            with patch("core.storage.ROOT", home), lock_data_directory():
                with self.assertRaises(DataDirectoryBusy), lock_data_directory():
                    self.fail("Le dossier doit rester verrouillé.")
                for script, expected_code in (("cli.py", 1), ("init_data.py", 1), ("app.py", 3), ("desktop_backend.py", 73)):
                    with self.subTest(script=script):
                        result = launch(script)
                        self.assertEqual(result.returncode, expected_code, result.stderr)
                        self.assertIn("déjà utilisé", result.stderr)
                self.assertEqual(launch("cli.py", "--list").returncode, 0)
                self.assertEqual(read_json(memory)["long_term"], ["à conserver"])
                self.assertFalse((home / "data" / "identity.json").exists())

            self.assertEqual(launch("init_data.py").returncode, 0)
            self.assertTrue((home / "data" / "identity.json").exists())
            self.assertEqual(read_json(memory)["long_term"], ["à conserver"])

    def test_lock_released_after_process_is_killed(self):
        with tempfile.TemporaryDirectory() as temp, patch("core.storage.ROOT", Path(temp)):
            child = subprocess.Popen(
                [sys.executable, "-c", "from core.storage import lock_data_directory\n"
                 "with lock_data_directory():\n print('locked', flush=True)\n input()"],
                cwd=ROOT, env={**os.environ, "AIRIMINI_HOME": temp},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            )
            with child:
                try:
                    self.assertEqual(child.stdout.readline().strip(), "locked")
                    with self.assertRaises(DataDirectoryBusy), lock_data_directory():
                        self.fail("Le verrou du sous-processus doit être actif.")
                finally:
                    child.kill()
                    child.wait(timeout=5)
                with lock_data_directory():
                    self.assertTrue((Path(temp) / "data" / ".airi.lock").is_file())


class HTTPTests(BrainFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import uvicorn
        from app import create_app
        for mock in (patch("core.storage.ROOT", self.root), patch("app.load_settings", return_value=self.brain.settings)):
            mock.start()
            self.addCleanup(mock.stop)
        self.app = create_app(lambda: self.brain, web_root=ROOT / 'web')
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.socket.listen(128)
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level='critical', access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 5
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() >= deadline:
                self.fail('Le serveur de test ne démarre pas')
            time.sleep(.01)
        self.token = self.request('/api/bootstrap')['token']

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)
        self.socket.close()

    def request(self, path, data=None, method=None, token=True, raw=False):
        headers = {'Content-Type': 'application/json'}
        if token and hasattr(self, 'token'):
            headers['X-Airi-Token'] = self.token
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}',
                                         data=json.dumps(data).encode() if data is not None else None,
                                         headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
            return body if raw else json.loads(body)

    def test_http_conversation_management_and_data_routes(self):
        self.brain.think('du jazz ce soir', self.a)
        self.assertEqual(self.request(f'/api/sessions/{self.a}', {'title': 'Soirée'}, 'PATCH')['title'], 'Soirée')
        results = self.request('/api/sessions/search?q=JAZZ')
        self.assertEqual([r['id'] for r in results], [self.a])
        self.request(f'/api/sessions/{self.b}', method='DELETE')
        self.assertEqual([s['id'] for s in self.request('/api/sessions')], [self.a])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(f'/api/sessions/{self.b}', method='DELETE')
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()
        bundle = self.request('/api/data/export')
        self.assertEqual(len(bundle['sessions']), 1)
        preview = self.request('/api/data/import', {'bundle': bundle, 'dry_run': True}, 'POST')
        self.assertEqual(preview['sessions_existing'], 1)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/data/import', {'bundle': {'format': 'autre'}}, 'POST')
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_http_regenerate_and_stop_saves_partial_reply(self):
        import http.client
        self.brain.think('question', self.a)
        self.llm.reply = ['Une ', 'nouvelle ', 'réponse']
        chunks = self.request('/api/chat/stream', {'session_id': self.a, 'message': 'question', 'replace_last': True}, 'POST', raw=True)
        self.assertEqual(json.loads(chunks.splitlines()[-1]), {'type': 'done'})
        self.assertEqual([m['content'] for m in self.request('/api/sessions/' + self.a)['messages']],
                         ['question', 'Une nouvelle réponse'])
        # Stop: the client closes the connection mid-stream; the server keeps what was generated.
        self.llm.reply = ['Début '] + ['suite '] * 200
        self.llm.piece_delay = .01
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request('POST', '/api/chat/stream', json.dumps({'session_id': self.a, 'message': 'longue'}),
                           {'Content-Type': 'application/json', 'X-Airi-Token': self.token})
        response = connection.getresponse()
        self.assertEqual(json.loads(response.readline())['type'], 'delta')
        response.close(); connection.close()
        messages = self.request('/api/sessions/' + self.a)['messages']
        self.assertEqual(messages[-2]['content'], 'longue')
        self.assertTrue(messages[-1]['interrupted'])
        self.assertTrue(messages[-1]['content'].startswith('Début '))
        self.assertLess(len(messages[-1]['content']), len('Début ' + 'suite ' * 200))

    def test_http_routes_stream_and_export(self):
        session = self.request('/api/sessions', {}, 'POST')
        chunks = self.request('/api/chat/stream', {'session_id': session['id'], 'message': 'bonjour'}, 'POST', raw=True)
        events = [json.loads(line) for line in chunks.splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(''.join(e.get('text', '') for e in events), 'Bonjour Haru !')
        exported = self.request('/api/sessions/' + session['id'])
        self.assertEqual(len(exported['messages']), 2)
        self.assertTrue(self.request('/api/health')['ready'])
        self.request('/api/memory', {'fact': 'test'}, 'POST')
        self.assertEqual(self.request('/api/memory')['facts'], ['test'])
        self.request('/api/memory', {'fact': 'test'}, 'DELETE')
        self.assertEqual(self.request('/api/memory')['facts'], [])
        self.llm.reply = ['Noté.', '\n[[retenir: Haru aime le thé]]']
        chunks = self.request('/api/chat/stream', {'session_id': session['id'], 'message': 'J\'aime le thé'}, 'POST', raw=True)
        events = [json.loads(line) for line in chunks.splitlines()]
        self.assertEqual(events[-2:], [{'type': 'suggestion', 'fact': 'Haru aime le thé'}, {'type': 'done'}])
        self.assertNotIn('retenir', ''.join(e.get('text', '') for e in events))
        self.assertEqual(self.request('/api/memory')['facts'], [])
        index = self.request('/', raw=True)
        self.assertIn(b'/assets/app.js', index)
        self.assertIn(b'/assets/logo.svg', index)
        self.assertIn(b'/assets/menus.css', index)
        self.assertIn(b'/assets/menus.js', index)
        self.assertIn(b'::picker(select)', self.request('/assets/menus.css', raw=True))
        self.assertEqual(self.request('/assets/logo.svg', raw=True), (ROOT / 'desktop' / 'logo.svg').read_bytes())
        self.assertIn(b'sendMessage', self.request('/assets/app.js', raw=True))

    def test_http_errors_and_local_protection(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/sessions', {}, 'POST', token=False)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/chat', {'message': ' ', 'session_id': self.a}, 'POST')
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()
        self.llm.fail = True
        chunks = self.request('/api/chat/stream', {'message': 'test', 'session_id': self.a}, 'POST', raw=True)
        self.assertEqual(json.loads(chunks.splitlines()[-1])['type'], 'error')
        self.assertTrue(self.brain.sessions.get(self.a).messages[-1].interrupted)

    @unittest.skipIf(os.name == 'nt', 'Windows ne synchronise pas le dossier dans ce chemin')
    def test_memory_api_reports_sync_failure_and_returns_committed_fact(self):
        with fail_directory_sync(), self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/memory', {'fact': 'souvenir conservé'}, 'POST')
        with closing(caught.exception) as response:
            self.assertEqual(response.code, 503)
            self.assertIn('Vérifie le résultat avant de réessayer', json.load(response)['detail'])
        self.assertEqual(self.request('/api/memory')['facts'], ['souvenir conservé'])

    def test_streamed_memory_pages_round_trip_with_101_long_facts(self):
        facts = [f'Souvenir {index:03d} '.ljust(1000, 'é') for index in range(101)]
        atomic_write_json(self.brain.memory_manager.path, {'long_term': facts})
        self.brain.memory_manager.load()
        displayed_facts = []
        responses = []
        for page in range(1, 12):
            command = '/souvenirs' if page == 1 else f'/souvenirs {page}'
            payload = {'session_id': self.a, 'message': command}
            chunks = self.request('/api/chat/stream', payload, 'POST', raw=True)
            events = [json.loads(line) for line in chunks.splitlines()]
            self.assertEqual(events[-1], {'type': 'done'})
            response = ''.join(event['text'] for event in events[:-1])
            self.assertIn(f'page {page}/11', response)
            displayed_facts.extend(line.split('. ', 1)[1] for line in response.splitlines()
                                   if line.split('. ', 1)[0].isdecimal())
            responses.append(response)

        self.assertEqual(displayed_facts, facts)
        session = self.request('/api/sessions/' + self.a)
        self.assertEqual([m['content'] for m in session['messages'] if m['role'] == 'assistant'], responses)
        self.assertFalse(self.llm.calls)

    def test_health_does_not_block_during_inference(self):
        self.llm.delay = .5
        self.llm.entered.clear()
        with ThreadPoolExecutor(1) as pool:
            job = pool.submit(self.request, '/api/chat', {'message': 'test', 'session_id': self.a}, 'POST')
            self.assertTrue(self.llm.entered.wait(2))
            start = time.monotonic()
            self.assertTrue(self.request('/api/health')['ready'])
            self.assertLess(time.monotonic() - start, .3)
            self.assertEqual(job.result()['response'], 'Bonjour Haru !')


class JobLimiterTests(unittest.TestCase):
    def test_limit_is_enforced_and_released(self):
        from app import JobLimiter
        limiter = JobLimiter(2)
        self.assertTrue(limiter.try_acquire())
        self.assertTrue(limiter.try_acquire())
        self.assertFalse(limiter.try_acquire())
        limiter.release()
        self.assertEqual(limiter.pending, 1)
        self.assertTrue(limiter.try_acquire())

    def test_concurrent_acquires_never_exceed_limit(self):
        from app import JobLimiter
        limiter = JobLimiter(8)
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: limiter.try_acquire(), range(200)))
        self.assertEqual(sum(results), 8)
        self.assertEqual(limiter.pending, 8)


if __name__ == '__main__':
    unittest.main()
