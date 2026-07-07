from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.database.dependencies import get_db
from app.services.asset_actions import AssetActionAlreadyRunning, publish_asset_updates_action
from app.web.guards import require_assets_feature_enabled

# Mounted without the enabled-guard: this route keeps the legacy
# plugin.mqtt.enabled fallback in addition to plugin.mqtt-hass.enabled.
ungated_router = APIRouter(tags=["mqtt"])


@ungated_router.post("/assets/mqtt-publish")
def assets_mqtt_publish_page(db: Session = Depends(get_db)):
    require_assets_feature_enabled(db)
    if get_setting_value(db, "plugin.mqtt-hass.enabled", get_setting_value(db, "plugin.mqtt.enabled", "false")) != "true":
        raise HTTPException(status_code=404, detail="Feature is disabled")
    try:
        publish_asset_updates_action(db, manual=True)
    except AssetActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}") from exc
    return RedirectResponse(url="/assets", status_code=303)
