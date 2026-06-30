from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.assets import Asset
from app.models.systems import System

SOURCE_PLUGIN = "apps_inventory"


def _slug(value: str) -> str:
    return "-".join(str(value).strip().lower().split())


def _system_external_id(vmid: str) -> str:
    return f"apps_inventory:system:{vmid}"


def _asset_external_id(system_external_id: str, name: str) -> str:
    return f"{system_external_id}:app:{_slug(name)}"


def import_apps_inventory(
    db: Session,
    inventory: dict[str, Any],
) -> dict[str, int]:
    imported_systems = 0
    imported_assets = 0
    updated_assets = 0
    inactive_assets = 0

    now = utc_now().replace(tzinfo=None)
    external_master = get_setting_value(db, "plugin.apps_inventory.apps_master", get_setting_value(db, "apps_master", "opensecdash")) == "external"

    for system_data in inventory.get("systems", []):
        vmid = str(system_data.get("vmid", "")).strip()
        hostname = str(system_data.get("hostname", "")).strip()
        system_type = str(system_data.get("type", "")).strip()

        if not vmid or not hostname:
            continue

        system_external_id = _system_external_id(vmid)
        system = db.query(System).filter(System.source_plugin == SOURCE_PLUGIN, System.external_id == system_external_id).first()
        if system is None:
            system = db.query(System).filter(System.vmid == vmid).first()

        if system is None:
            system = System(
                vmid=vmid,
                hostname=hostname,
                system_type=system_type,
                source_plugin=SOURCE_PLUGIN,
                external_id=system_external_id,
            )
            db.add(system)
            db.flush()
            imported_systems += 1
        else:
            system.hostname = hostname
            system.system_type = system_type
            system.source_plugin = system.source_plugin or SOURCE_PLUGIN
            system.external_id = system.external_id or system_external_id

        seen_asset_names: set[str] = set()

        for app_data in system_data.get("apps", []):
            name = str(app_data.get("name", "")).strip()
            version = str(app_data.get("version", "")).strip()
            release_url = str(app_data.get("release_url") or app_data.get("url") or "").strip() or None
            app_url = str(app_data.get("app_url") or app_data.get("homepage") or "").strip() or None
            host_url = str(app_data.get("host_url") or app_data.get("host") or "").strip() or None

            if not name:
                continue

            seen_asset_names.add(name)

            asset_external_id = _asset_external_id(system_external_id, name)
            asset = (
                db.query(Asset)
                .filter(Asset.source_plugin == SOURCE_PLUGIN, Asset.external_id == asset_external_id)
                .first()
            )
            if asset is None:
                asset = (
                    db.query(Asset)
                    .filter(
                        Asset.system_id == system.id,
                        Asset.name == name,
                    )
                    .first()
                )

            if asset is None:
                asset = Asset(
                    system_id=system.id,
                    name=name,
                    version=version,
                    release_url=release_url,
                    url=app_url,
                    host_url=host_url,
                    latest_version=None,
                    update_available=False,
                    is_active=True,
                    source_plugin=SOURCE_PLUGIN,
                    external_id=asset_external_id,
                    last_seen=now,
                )
                db.add(asset)
                imported_assets += 1
            else:
                asset.source_plugin = asset.source_plugin or SOURCE_PLUGIN
                asset.external_id = asset.external_id or asset_external_id
                if external_master:
                    asset.version = version
                    asset.release_url = release_url
                asset.url = app_url
                if external_master:
                    asset.host_url = host_url
                asset.is_active = True
                asset.last_seen = now
                updated_assets += 1

        existing_assets = (
            db.query(Asset)
            .filter(Asset.system_id == system.id, Asset.source_plugin == SOURCE_PLUGIN)
            .all()
        )

        for asset in existing_assets:
            if asset.name not in seen_asset_names and asset.is_active:
                asset.is_active = False
                inactive_assets += 1

    db.commit()

    return {
        "systems_created": imported_systems,
        "assets_created": imported_assets,
        "assets_updated": updated_assets,
        "assets_inactive": inactive_assets,
    }
