from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.i18n import translate
from app.core.language import get_language
from app.models.settings import Setting


def get_setting_value(
    db: Session,
    key: str,
    default: str = "",
) -> str:
    setting = (
        db.query(Setting)
        .filter(Setting.key == key)
        .first()
    )

    if setting is None:
        return default

    return setting.value


def build_template_context(db: Session) -> dict[str, str | Callable[[str], str]]:
    language = get_language(db)
    domain = get_setting_value(db, "domain", "")

    return {
        "language": language,
        "domain": domain,
        "t": lambda key: translate(key, language),
    }
