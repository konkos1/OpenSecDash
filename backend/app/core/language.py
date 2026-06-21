from sqlalchemy.orm import Session

from app.models.settings import Setting


def get_language(db: Session) -> str:
    setting = (
        db.query(Setting)
        .filter(Setting.key == "language")
        .first()
    )

    if setting is None:
        return "en"

    return setting.value
