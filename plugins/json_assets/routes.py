from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.database.dependencies import get_db
from app.services.asset_actions import AssetActionAlreadyRunning

from .services.actions import import_assets_inventory_action, import_assets_source_action

# Enabled-gated by the plugin router mount (see app.main); no require_plugin_enabled here.
router = APIRouter(tags=["json_assets"])
api_router = APIRouter(prefix="/api/assets", tags=["assets"])


def asset_action_conflict(exc: AssetActionAlreadyRunning) -> HTTPException:
    return HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}")


class AssetImportRequest(BaseModel):
    inventory: dict[str, Any]


@router.post("/assets/import-source")
def assets_import_source_page(db: Session = Depends(get_db)):
    source_type = get_setting_value(
        db,
        "plugin.json_assets.source_type",
        get_setting_value(db, "plugin.assets.source_type", get_setting_value(db, "asset_source_type", "file")),
    )
    source = get_setting_value(
        db,
        "plugin.json_assets.source",
        get_setting_value(db, "plugin.assets.source", get_setting_value(db, "asset_source", "/assets/assets.json")),
    )
    if source:
        try:
            import_assets_source_action(db=db, source_type=source_type, source=source)
        except AssetActionAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}") from exc
    return RedirectResponse(url="/assets", status_code=303)


@api_router.post("/import")
def import_assets(
    payload: AssetImportRequest,
    db: Session = Depends(get_db),
):
    try:
        return import_assets_inventory_action(db=db, inventory=payload.inventory)
    except AssetActionAlreadyRunning as exc:
        raise asset_action_conflict(exc) from exc


@api_router.post("/import-source")
def import_assets_from_source(
    db: Session = Depends(get_db),
):
    source_type = get_setting_value(
        db,
        "plugin.json_assets.source_type",
        get_setting_value(db, "plugin.assets.source_type", get_setting_value(db, "asset_source_type", "url")),
    )

    source = get_setting_value(
        db,
        "plugin.json_assets.source",
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


router.include_router(api_router)
