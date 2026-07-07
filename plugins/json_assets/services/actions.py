from typing import Any

from sqlalchemy.orm import Session

from app.services.asset_actions import export_publishable_asset_updates, run_asset_action

from .importer import import_json_assets
from .source import load_asset_source


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
