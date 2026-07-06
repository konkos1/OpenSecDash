from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.settings import Setting
from app.services.github_releases import get_latest_github_release

logger = logging.getLogger(__name__)

UPDATE_CHECK_ENABLED_KEY = "update_check_enabled"
LATEST_VERSION_KEY = "update_check.latest_version"
CHECKED_AT_KEY = "update_check.checked_at"
OPENSECDASH_REPO = "konkos1/OpenSecDash"


def self_update_check_enabled(db: Session) -> bool:
    return get_setting_value(db, UPDATE_CHECK_ENABLED_KEY, "true") == "true"


def _save_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def run_self_update_check(db: Session) -> str | None:
    """Query GitHub for the latest OpenSecDash release and persist the result.

    Returns the latest known version tag (or None when the check failed or is
    disabled). The result is stored in settings so the footer can read it on
    every page render without ever making a network call itself, and so the
    last known answer survives restarts.
    """
    if not self_update_check_enabled(db):
        return None
    try:
        # Reuse the asset-update GitHub token when one is configured: it
        # raises the API rate limit, and this endpoint needs no scopes.
        latest = get_latest_github_release(repo=OPENSECDASH_REPO, github_token=get_setting_value(db, "asset_updates.github_token", "") or None)
    except Exception as exc:
        logger.warning("OpenSecDash update check failed: %s", exc)
        return None
    if not latest:
        return None
    _save_setting(db, LATEST_VERSION_KEY, str(latest))
    _save_setting(db, CHECKED_AT_KEY, utc_now().replace(tzinfo=None).isoformat())
    db.commit()
    logger.debug("OpenSecDash update check: latest release is %s", latest)
    return str(latest)
