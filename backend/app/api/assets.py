from fastapi import APIRouter, Depends
from fastapi import HTTPException

from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.assets import Asset

from app.services.asset_actions import (
    AssetActionAlreadyRunning,
    refresh_asset_updates_action,
)


router = APIRouter(
    prefix="/api/assets",
    tags=["assets"],
)


def asset_action_conflict(exc: AssetActionAlreadyRunning) -> HTTPException:
    return HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}")


@router.post("/refresh-updates")
def refresh_updates(
    db: Session = Depends(get_db),
):
    try:
        return refresh_asset_updates_action(db)
    except AssetActionAlreadyRunning as exc:
        raise asset_action_conflict(exc) from exc


@router.get("")
def list_assets(db: Session = Depends(get_db)):
    assets = (
        db.query(Asset)
        .order_by(Asset.name)
        .all()
    )

    return [
        {
            "id": asset.id,
            "system_id": asset.system_id,
            "name": asset.name,
            "version": asset.version,
            "host_url": asset.host_url,
            "latest_version": asset.latest_version,
            "release_url": asset.release_url,
            "update_available": asset.update_available,
        }
        for asset in assets
    ]
