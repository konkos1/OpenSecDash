from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import plugin_registry
from app.core.template_context import get_setting_value
from app.database.dependencies import get_db


def is_plugin_enabled(db: Session, plugin_id: str) -> bool:
    # A plugin turned off via OSD_PLUGIN_*_DISABLED is not loaded, so it is
    # never "enabled" - even if its plugin.<id>.enabled setting still says true
    # from before it was disabled. This keeps nav, feature flags and per-page
    # enabled maps (dashboard, IP explorer) consistent with discovery.
    if not plugin_registry.is_registered(plugin_id):
        return False
    return get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"


def events_feature_enabled(db: Session) -> bool:
    return any(is_plugin_enabled(db, plugin_id) for plugin_id in plugin_registry.ids_with_capability("datasource"))


def assets_feature_enabled(db: Session) -> bool:
    return any(is_plugin_enabled(db, plugin_id) for plugin_id in plugin_registry.ids_with_capability("asset_source"))


def require_plugin_enabled(db: Session, plugin_id: str) -> None:
    if not is_plugin_enabled(db, plugin_id):
        raise HTTPException(status_code=404, detail="Feature is disabled")


def require_events_feature_enabled(db: Session) -> None:
    if not events_feature_enabled(db):
        raise HTTPException(status_code=404, detail="Feature is disabled")


def require_assets_feature_enabled(db: Session) -> None:
    if not assets_feature_enabled(db):
        raise HTTPException(status_code=404, detail="Feature is disabled")


def plugin_enabled_guard(plugin_id: str):
    """FastAPI dependency factory: 404s a plugin router when its plugin is off."""

    def _guard(db: Session = Depends(get_db)) -> None:
        require_plugin_enabled(db, plugin_id)

    return _guard
