from typing import Any, TypeAlias

import requests
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now

from app.models.assets import Asset

from app.services.github_releases import get_latest_github_release
from app.services.github_releases import github_repo_from_url

ReleaseCache: TypeAlias = dict[str, tuple[bool, str | None, str | None]]


def _github_token(db: Session) -> str:
    return get_setting_value(db, "asset_updates.github_token", "")


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


def _release_error_reason(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        response = exc.response
        body = (response.text or "").strip()
        if response.status_code == 403 and (response.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in body.lower()):
            return "GitHub rate limit exceeded"
        if body:
            return f"GitHub API HTTP {response.status_code}: {body[:200]}"
        return f"GitHub API HTTP {response.status_code}"
    return str(exc) or exc.__class__.__name__


def _cached_latest_release(db: Session, repo: str, cache: ReleaseCache | None) -> tuple[bool, str | None, str | None]:
    if cache is not None and repo in cache:
        return cache[repo]
    try:
        result = (True, get_latest_github_release(repo=repo, github_token=_github_token(db)), None)
    except Exception as exc:
        result = (False, None, _release_error_reason(exc))
    if cache is not None:
        cache[repo] = result
    return result


def refresh_asset_update(db: Session, asset: Asset, release_cache: ReleaseCache | None = None) -> dict[str, Any]:
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

    ok, latest_version, error = _cached_latest_release(db, repo, release_cache)
    if not ok:
        return {"checked": 1, "updated": 0, "failed": 1, "failed_reason": error or "unknown error"}

    if not latest_version:
        return {"checked": 1, "updated": 0, "failed": 0}

    return {"checked": 1, "updated": 1 if _apply_update_state(asset, latest_version) else 0, "failed": 0}


def refresh_asset_updates(db: Session) -> dict[str, Any]:
    checked = 0
    updated = 0
    failed = 0
    failed_assets: list[str] = []
    failed_reasons: list[str] = []

    assets = db.query(Asset).all()
    release_cache: ReleaseCache = {}

    for asset in assets:
        result = refresh_asset_update(db, asset, release_cache)
        checked += result["checked"]
        updated += result["updated"]
        failed += result["failed"]
        if result["failed"]:
            repo = github_repo_from_url(asset.release_url)
            reason = str(result.get("failed_reason") or "unknown error")
            failed_assets.append(f"{asset.name} ({repo or asset.release_url or 'unknown release URL'}: {reason})")
            if reason not in failed_reasons:
                failed_reasons.append(reason)

    db.commit()

    return {
        "checked": checked,
        "updated": updated,
        "failed": failed,
        "failed_assets": failed_assets,
        "failed_reasons": failed_reasons,
    }
