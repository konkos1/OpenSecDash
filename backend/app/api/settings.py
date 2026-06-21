from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.settings import Setting

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
)


@router.get("")
def list_settings(db: Session = Depends(get_db)):
    settings = db.query(Setting).order_by(Setting.key).all()

    return [
        {
            "key": setting.key,
            "value": setting.value,
        }
        for setting in settings
    ]


@router.get("/{key}")
def get_setting(key: str, db: Session = Depends(get_db)):
    setting = db.query(Setting).filter(Setting.key == key).first()

    if setting is None:
        return {
            "key": key,
            "value": None,
        }

    return {
        "key": setting.key,
        "value": setting.value,
    }


@router.put("/{key}")
def set_setting(
    key: str,
    value: str,
    db: Session = Depends(get_db),
):
    setting = db.query(Setting).filter(Setting.key == key).first()

    if setting is None:
        setting = Setting(
            key=key,
            value=value,
        )
        db.add(setting)
    else:
        setting.value = value

    db.commit()

    return {
        "key": setting.key,
        "value": setting.value,
    }
