from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.assets import Asset
from app.models.systems import System


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

        system = db.query(System).filter(System.vmid == vmid).first()

        if system is None:
            system = System(
                vmid=vmid,
                hostname=hostname,
                system_type=system_type,
            )
            db.add(system)
            db.flush()
            imported_systems += 1
        else:
            system.hostname = hostname
            system.system_type = system_type

        seen_asset_names: set[str] = set()

        for app_data in system_data.get("apps", []):
            name = str(app_data.get("name", "")).strip()
            version = str(app_data.get("version", "")).strip()
            release_url = str(app_data.get("release_url") or app_data.get("url") or "").strip() or None
            app_url = str(app_data.get("app_url") or app_data.get("homepage") or "").strip() or None

            if not name:
                continue

            seen_asset_names.add(name)

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
                    latest_version=None,
                    update_available=False,
                    is_active=True,
                    last_seen=now,
                )
                db.add(asset)
                imported_assets += 1
            else:
                if external_master:
                    asset.version = version
                    asset.release_url = release_url
                asset.url = app_url
                asset.is_active = True
                asset.last_seen = now
                updated_assets += 1

        existing_assets = (
            db.query(Asset)
            .filter(Asset.system_id == system.id)
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
