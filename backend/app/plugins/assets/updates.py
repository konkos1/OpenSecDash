from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value

from app.models.assets import Asset

from app.services.github_releases import get_latest_github_release
from app.services.github_releases import github_repo_from_url


def refresh_asset_updates(db: Session) -> dict[str, int]:
    checked = 0
    updated = 0
    failed = 0

    assets = db.query(Asset).all()

    github_token = get_setting_value(db, "github_token", "")

    for asset in assets:
        repo = github_repo_from_url(asset.release_url)

        if repo is None:
            continue

        checked += 1

        try:
            latest_version = get_latest_github_release(
                repo=repo,
                github_token=github_token,
            )
        except Exception:
            failed += 1
            continue

        if not latest_version:
            continue

        asset.latest_version = latest_version
        asset.update_available = (
            latest_version.strip().lower()
            != asset.version.strip().lower()
        )

        updated += 1

    db.commit()

    return {
        "checked": checked,
        "updated": updated,
        "failed": failed,
    }
