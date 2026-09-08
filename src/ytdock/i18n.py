"""Independent locale catalogs with stable English keys and English fallback."""

import json
from pathlib import Path

DEFAULT_LANGUAGE = "en"
LOCALES_DIRECTORY = Path(__file__).with_name("locales")
CATALOGS = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(LOCALES_DIRECTORY.glob("*.json"))
}
LANGUAGES = (DEFAULT_LANGUAGE, *(code for code in CATALOGS if code != DEFAULT_LANGUAGE))
_language = DEFAULT_LANGUAGE


def get_language():
    return _language


def set_language(language):
    if language not in CATALOGS:
        raise ValueError(f"Unsupported language: {language}")
    global _language
    _language = language


def language_name(language):
    return CATALOGS[language]["language.name"]


def t(key, **values):
    template = CATALOGS[_language].get(key)
    if template is None:
        template = CATALOGS[DEFAULT_LANGUAGE][key]
    return template.format(**values) if values else template
