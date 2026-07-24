"""Personal display preference defaults and resolution."""
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.core.i18n import available_languages
from app.core.secrets import decrypt_setting_value
from app.models.settings import Setting
from app.models.users import User, UserPreference

PREFERENCE_DEFAULTS = {
    "language": "en",
    "live_default": "true",
    "theme": "auto",
    "accent_color": "blue",
    "live_page_refresh": "true",
}
_GLOBAL_SETTING_KEYS = {
    "language": "language",
    "live_default": "live_default",
    "theme": "theme",
    "accent_color": "instance_accent_color",
    "live_page_refresh": "live_page_refresh",
}
_ALLOWED_VALUES = {
    "live_default": {"true", "false"},
    "theme": {"auto", "dark", "light"},
    "accent_color": {"blue", "green", "orange", "red"},
    "live_page_refresh": {"true", "false"},
}


def allowed_preference_values(key: str) -> set[str]:
    """Return the accepted values for one preference.

    The languages come from the locale registry instead of a second allowlist,
    so registering another complete core locale makes it selectable everywhere
    without touching this validation.
    """
    return set(available_languages()) if key == "language" else _ALLOWED_VALUES[key]


def normalize_preferences(values: Mapping[str, object]) -> dict[str, str]:
    """Return the five preferences with invalid or missing values defaulted."""
    return {
        key: str(values.get(key, PREFERENCE_DEFAULTS[key]))
        if str(values.get(key, PREFERENCE_DEFAULTS[key])) in allowed_preference_values(key)
        else PREFERENCE_DEFAULTS[key]
        for key in PREFERENCE_DEFAULTS
    }


def global_preferences(db: Session) -> dict[str, str]:
    """Read and normalize the global defaults retained for auth-free use."""
    setting_keys = set(_GLOBAL_SETTING_KEYS.values())
    stored_values = {
        setting.key: decrypt_setting_value(setting.key, setting.value)
        for setting in db.query(Setting).filter(Setting.key.in_(setting_keys)).all()
    }
    return normalize_preferences(
        {
            preference_key: stored_values.get(setting_key, PREFERENCE_DEFAULTS[preference_key])
            for preference_key, setting_key in _GLOBAL_SETTING_KEYS.items()
        }
    )


def create_user_preferences(db: Session, user_id: int) -> UserPreference:
    """Add one new user's preferences from the current global defaults."""
    preferences = UserPreference(user_id=user_id, **global_preferences(db))
    db.add(preferences)
    return preferences


def effective_preferences(db: Session, user: User | None) -> dict[str, str]:
    """Resolve display preferences for a signed-in user or an anonymous request."""
    if user is None:
        return global_preferences(db)
    preferences = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
    if preferences is None:
        return global_preferences(db)
    return normalize_preferences(
        {
            "language": preferences.language,
            "live_default": preferences.live_default,
            "theme": preferences.theme,
            "accent_color": preferences.accent_color,
            "live_page_refresh": preferences.live_page_refresh,
        }
    )
