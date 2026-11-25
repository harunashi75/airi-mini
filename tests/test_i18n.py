"""Every translation key used in the code exists, and a second language can be plugged in."""
import ast
from pathlib import Path
import sys
import types
import unittest

from core import i18n
from core.i18n import t
from core.locales import fr
from core.storage import ROOT


def used_keys():
    keys = set()
    for path in [*Path(ROOT, 'core').rglob('*.py'), Path(ROOT, 'app.py'), Path(ROOT, 'cli.py')]:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 't' and node.args:
                argument = node.args[0]
                if isinstance(argument, ast.Constant):
                    keys.add(argument.value)
                elif isinstance(argument, ast.JoinedStr):  # t(f"{command}.done")
                    keys.update({'activity.done', 'mood.done'})
    return keys


class I18nTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language('fr')
        sys.modules.pop('core.locales.xx', None)
        i18n._catalogs.pop('xx', None)

    def test_every_used_key_exists_in_french(self):
        keys = used_keys()
        self.assertGreater(len(keys), 80)
        self.assertEqual(sorted(keys - fr.STRINGS.keys()), [])

    def test_french_placeholders_are_well_formed(self):
        import string
        for key, text in fr.STRINGS.items():
            with self.subTest(key=key):
                list(string.Formatter().parse(text))

    def test_other_language_with_french_fallback(self):
        catalog = types.ModuleType('core.locales.xx')
        catalog.STRINGS = {'memories.empty': 'No memories yet.', 'session.default_title': 'New chat'}
        catalog.COMMANDS = {**fr.COMMANDS, 'help': '/help', 'memories': '/memories'}
        sys.modules['core.locales.xx'] = catalog
        i18n.set_language('xx')
        self.assertEqual(t('memories.empty'), 'No memories yet.')
        self.assertEqual(t('forget.done'), fr.STRINGS['forget.done'])  # fallback
        self.assertEqual(i18n.value('COMMANDS')['help'], '/help')
        self.assertIs(i18n.value('STOPWORDS'), fr.STOPWORDS)  # fallback for a whole entry
        self.assertEqual(i18n.all_values('session.default_title'), {'Nouvelle conversation', 'New chat'})

    def test_unknown_language_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'non disponible'):
            i18n.set_language('zz')
        with self.assertRaisesRegex(ValueError, 'invalide'):
            i18n.set_language('../etc')
        self.assertEqual(i18n.language(), 'fr')


if __name__ == '__main__':
    unittest.main()
