"""User-facing text lives in core/locales/<langue>.py; this module picks the active catalog.

To add a language: copy core/locales/fr.py to core/locales/<code>.py, translate the values
(keep the keys and the {placeholders}), then set "language": "<code>" in config/airi.json.
Missing keys fall back to French, so a partial translation still works.
"""
import importlib
import re

DEFAULT = "fr"
_language = DEFAULT
_catalogs = {}


def _catalog(language):
    if language not in _catalogs:
        if not re.fullmatch(r"[a-z]{2,3}(_[A-Z]{2})?", language):
            raise ValueError(f"Code de langue invalide : {language}")
        try:
            _catalogs[language] = importlib.import_module(f"core.locales.{language}")
        except ModuleNotFoundError as exc:
            raise ValueError(f"Langue non disponible : {language} (fichier core/locales/{language}.py absent).") from exc
    return _catalogs[language]


def set_language(language):
    global _language
    _catalog(language)
    _language = language


def language():
    return _language


def value(name):
    """A whole catalog entry (STRINGS, COMMANDS, STOPWORDS…) for the active language, French as fallback."""
    return getattr(_catalog(_language), name, None) or getattr(_catalog(DEFAULT), name)


def t(key, **values):
    """Translated text for `key`, formatted with `values`."""
    text = _catalog(_language).STRINGS.get(key)
    if text is None:
        text = _catalog(DEFAULT).STRINGS[key]
    return text.format(**values) if values else text


def all_values(key):
    """`key` in every loaded language, e.g. to recognise default titles saved under another language."""
    _catalog(DEFAULT)
    return {catalog.STRINGS[key] for catalog in _catalogs.values() if key in catalog.STRINGS}
