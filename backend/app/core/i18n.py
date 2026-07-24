from typing import TypedDict

from app.locales import LOCALES

# Plugins register their own translations here during discovery (see the plugin
# manager). They act as a fallback layer *below* the core LOCALES: a key defined
# in both is resolved from core, so a plugin can never override a core string.
_EXTRA_LOCALES: dict[str, dict[str, str]] = {}

# Every core locale names itself under this key, so a selectable language can be
# labelled in its own language without a second hand-maintained list.
LANGUAGE_SELF_NAME_KEY = "language.self_name"
DEFAULT_LANGUAGE = "en"


class LanguageOption(TypedDict):
    code: str
    label: str


def available_languages() -> tuple[str, ...]:
    """Return the registered core languages in a deterministic order.

    Plugin locale fragments deliberately do not count: they translate a few
    plugin strings, not the whole core interface, so they cannot make a
    language selectable.
    """
    return tuple(sorted(LOCALES))


def is_available_language(language: str) -> bool:
    """Return whether a submitted language code is a registered core language."""
    return language in LOCALES


def resolve_language(language: str) -> str:
    """Return the language to render in, falling back to English."""
    return language if is_available_language(language) else DEFAULT_LANGUAGE


def language_options() -> list[LanguageOption]:
    """Return the selectable core languages labelled in their own language."""
    return [{"code": code, "label": LOCALES[code].get(LANGUAGE_SELF_NAME_KEY, code)} for code in available_languages()]


def clear_extra_locales() -> None:
    """Remove all plugin-provided translations before plugin re-discovery."""
    _EXTRA_LOCALES.clear()


def register_extra_locales(locales: dict[str, dict[str, str]]) -> None:
    """Merge plugin-provided translations in as a fallback layer below core LOCALES."""
    for language, entries in locales.items():
        _EXTRA_LOCALES.setdefault(language, {}).update(entries)


def translate(
    key: str,
    language: str = "en",
) -> str:
    locale = LOCALES.get(language, LOCALES["en"])
    if key in locale:
        return locale[key]

    extra = _EXTRA_LOCALES.get(language, {})
    if key in extra:
        return extra[key]

    return _EXTRA_LOCALES.get("en", {}).get(key, key)
