"""Dialog text loaded from ``INI/Language.ini``."""

from configparser import ConfigParser

from .paths import INI_DIR

LANGUAGE_PATH = INI_DIR / "Language.ini"


def _load_language():
    parser = ConfigParser(interpolation=None)
    if not parser.read(LANGUAGE_PATH, encoding="utf-8"):
        raise FileNotFoundError(f"Language file not found: {LANGUAGE_PATH}")
    return parser


LANGUAGE = _load_language()


def text(section, key, **values):
    """Return a dialog text and substitute optional named placeholders."""
    value = LANGUAGE[section][key].replace(r"\n", "\n")
    return value.format(**values) if values else value
