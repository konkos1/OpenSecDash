from typing import TypeAlias

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now

from app.models.assets import Asset

from app.services.github_releases import get_latest_github_release
from app.services.github_releases import github_repo_from_url

ReleaseCache: TypeAlias = dict[str, tuple[bool, str | None]]


def _github_token(db: Session) -> str:
    return get_setting_value(
        db,
        "plugin.apps_inventory.github_token",
        get_setting_value(db, "plugin.assets.github_token", get_setting_value(db, "github_token", "")),
    )


def _apply_update_state(asset: Asset, latest_version: str | None) -> bool:
    if not latest_version:
        asset.latest_version = None
        asset.update_available = False
        asset.last_checked = utc_now().replace(tzinfo=None)
        return False
    asset.latest_version = latest_version
    asset.last_checked = utc_now().replace(tzinfo=None)
    if not asset.version:
        asset.update_available = False
        return True
    asset.update_available = latest_version.strip().lower() != asset.version.strip().lower()
    return True


def _cached_latest_release(db: Session, repo: str, cache: ReleaseCache | None) -> tuple[bool, str | None]:
    if cache is not None and repo in cache:
        return cache[repo]
    try:
        result = (True, get_latest_github_release(repo=repo, github_token=_github_token(db)))
    except Exception:
        result = (False, None)
    if cache is not None:
        cache[repo] = result
    return result


def refresh_asset_update(db: Session, asset: Asset, release_cache: ReleaseCache | None = None) -> dict[str, int]:
    """Refresh update metadata for one asset after user edits or imports.

    This intentionally recalculates ``update_available`` from the currently
    stored ``latest_version`` before making a network request. That keeps the UI
    correct when a user simply changes the installed version to match an already
    known latest release, and it also makes this helper easy to unit test later.
    """
    if asset.latest_version:
        _apply_update_state(asset, asset.latest_version)

    repo = github_repo_from_url(asset.release_url)
    if repo is None:
        asset.latest_version = None
        asset.update_available = False
        return {"checked": 0, "updated": 0, "failed": 0}

    ok, latest_version = _cached_latest_release(db, repo, release_cache)
    if not ok:
        return {"checked": 1, "updated": 0, "failed": 1}

    if not latest_version:
        return {"checked": 1, "updated": 0, "failed": 0}

    return {"checked": 1, "updated": 1 if _apply_update_state(asset, latest_version) else 0, "failed": 0}


def refresh_asset_updates(db: Session) -> dict[str, int]:
    checked = 0
    updated = 0
    failed = 0

    assets = db.query(Asset).all()
    release_cache: ReleaseCache = {}

    for asset in assets:
        result = refresh_asset_update(db, asset, release_cache)
        checked += result["checked"]
        updated += result["updated"]
        failed += result["failed"]

    db.commit()

    return {
        "checked": checked,
        "updated": updated,
        "failed": failed,
    }
