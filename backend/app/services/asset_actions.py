from collections.abc import Callable
from threading import Lock
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from app.models.assets import Asset
from app.plugins.manager import get_plugin_manager
from app.services.json_assets_import import import_json_assets
from app.services.json_assets_source import load_asset_source
from app.services.json_assets_updates import refresh_asset_updates

T = TypeVar("T")

_ASSET_ACTION_LOCK = Lock()
_RUNNING_ASSET_ACTION: str | None = None
_ASSET_METADATA_LOCKS: dict[int, Lock] = {}
_ASSET_METADATA_LOCKS_LOCK = Lock()


class AssetActionAlreadyRunning(Exception):
    def __init__(self, action: str):
        self.action = action
        super().__init__(f"Asset action is already running: {action}")


def current_asset_action() -> str | None:
    return _RUNNING_ASSET_ACTION


def asset_action_running(action: str | None = None) -> bool:
    if action is None:
        return _ASSET_ACTION_LOCK.locked()
    return _RUNNING_ASSET_ACTION == action


def run_asset_action(action: str, callback: Callable[[], T]) -> T:
    global _RUNNING_ASSET_ACTION
    if not _ASSET_ACTION_LOCK.acquire(blocking=False):
        raise AssetActionAlreadyRunning(_RUNNING_ASSET_ACTION or action)
    _RUNNING_ASSET_ACTION = action
    try:
        return callback()
    finally:
        _RUNNING_ASSET_ACTION = None
        _ASSET_ACTION_LOCK.release()


def run_asset_metadata_action(asset_id: int, callback: Callable[[], T]) -> T:
    with _ASSET_METADATA_LOCKS_LOCK:
        lock = _ASSET_METADATA_LOCKS.setdefault(asset_id, Lock())
    if not lock.acquire(blocking=False):
        raise AssetActionAlreadyRunning(f"metadata:{asset_id}")
    try:
        return callback()
    finally:
        lock.release()


def export_publishable_asset_updates(db: Session, *, manual: bool = False) -> None:
    import asyncio

    manager = get_plugin_manager()
    publishable_assets = db.query(Asset).filter(
        Asset.mqtt_publish_enabled == True,
        Asset.version.isnot(None),
        Asset.latest_version.isnot(None),
        Asset.release_url.isnot(None),
    ).all()
    for asset in publishable_assets:
        asyncio.run(manager.export_asset_update(db, asset, manual=manual))


def import_assets_inventory_action(db: Session, inventory: dict[str, Any]) -> Any:
    def action() -> Any:
        result = import_json_assets(db=db, inventory=inventory)
        export_publishable_asset_updates(db)
        return result

    return run_asset_action("import", action)


def import_assets_source_action(db: Session, *, source_type: str, source: str) -> Any:
    def action() -> Any:
        inventory = load_asset_source(source_type=source_type, source=source)
        result = import_json_assets(db=db, inventory=inventory)
        export_publishable_asset_updates(db)
        return result

    return run_asset_action("import", action)


def refresh_asset_updates_action(db: Session) -> Any:
    def action() -> Any:
        result = refresh_asset_updates(db)
        export_publishable_asset_updates(db)
        return result

    return run_asset_action("refresh_updates", action)


def publish_asset_updates_action(db: Session, *, manual: bool = True) -> None:
    def action() -> None:
        export_publishable_asset_updates(db, manual=manual)

    return run_asset_action("mqtt_publish", action)
