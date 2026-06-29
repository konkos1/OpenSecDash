from typing import Any

from fastapi import APIRouter, Depends
from fastapi import HTTPException

from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value

from app.database.dependencies import get_db
from app.models.assets import Asset

from app.services.asset_actions import (
    AssetActionAlreadyRunning,
    import_assets_inventory_action,
    import_assets_source_action,
    refresh_asset_updates_action,
)


router = APIRouter(
    prefix="/api/assets",
    tags=["assets"],
)


def asset_action_conflict(exc: AssetActionAlreadyRunning) -> HTTPException:
    return HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}")


class AssetImportRequest(BaseModel):
    inventory: dict[str, Any]


@router.post("/import")
def import_assets(
    payload: AssetImportRequest,
    db: Session = Depends(get_db),
):
    try:
        return import_assets_inventory_action(db=db, inventory=payload.inventory)
    except AssetActionAlreadyRunning as exc:
        raise asset_action_conflict(exc) from exc


@router.post("/import-source")
def import_assets_from_source(
    db: Session = Depends(get_db),
):
    source_type = get_setting_value(
        db,
        "plugin.apps_inventory.source_type",
        get_setting_value(db, "plugin.assets.source_type", get_setting_value(db, "asset_source_type", "url")),
    )

    source = get_setting_value(
        db,
        "plugin.apps_inventory.source",
        get_setting_value(db, "plugin.assets.source", get_setting_value(db, "asset_source", "")),
    )

    if not source:
        raise HTTPException(
            status_code=400,
            detail="asset_source is not configured",
        )

    try:
        return import_assets_source_action(db=db, source_type=source_type, source=source)
    except AssetActionAlreadyRunning as exc:
        raise asset_action_conflict(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
