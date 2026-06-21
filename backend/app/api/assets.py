from typing import Any

from fastapi import APIRouter, Depends
from fastapi import HTTPException

from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value

from app.database.dependencies import get_db

from app.models.assets import Asset

from app.plugins.assets.importer import import_apps_inventory
from app.plugins.assets.source import load_asset_source
from app.plugins.assets.updates import refresh_asset_updates


router = APIRouter(
    prefix="/api/assets",
    tags=["assets"],
)


class AssetImportRequest(BaseModel):
    inventory: dict[str, Any]


@router.post("/import")
def import_assets(
    payload: AssetImportRequest,
    db: Session = Depends(get_db),
):
    return import_apps_inventory(
        db=db,
        inventory=payload.inventory,
    )


@router.post("/import-source")
def import_assets_from_source(
    db: Session = Depends(get_db),
):
    source_type = get_setting_value(
        db,
        "asset_source_type",
        "url",
    )

    source = get_setting_value(
        db,
        "asset_source",
        "",
    )

    if not source:
        raise HTTPException(
            status_code=400,
            detail="asset_source is not configured",
        )

    try:
        inventory = load_asset_source(
            source_type=source_type,
            source=source,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    return import_apps_inventory(
        db=db,
        inventory=inventory,
    )


@router.post("/refresh-updates")
def refresh_updates(
    db: Session = Depends(get_db),
):
    return refresh_asset_updates(db)


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
            "latest_version": asset.latest_version,
            "release_url": asset.release_url,
            "update_available": asset.update_available,
        }
        for asset in assets
    ]
