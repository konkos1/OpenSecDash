from app.locales import LOCALES

# Plugins register their own translations here during discovery (see the plugin
# manager). They act as a fallback layer *below* the core LOCALES: a key defined
# in both is resolved from core, so a plugin can never override a core string.
_EXTRA_LOCALES: dict[str, dict[str, str]] = {}


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
