from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.database.dependencies import get_db
from app.models.core import Diagnostic
from app.services.asset_actions import AssetActionAlreadyRunning, export_publishable_asset_updates, run_asset_action

from .services.sync import sync_proxmox_assets

# Enabled-gated by the plugin router mount (see app.main); no require_plugin_enabled here.
router = APIRouter(tags=["proxmox_assets"])


@router.post("/assets/proxmox-sync")
def assets_proxmox_sync_page(db: Session = Depends(get_db)):
    try:
        run_asset_action(
            "proxmox_sync",
            lambda: (
                sync_proxmox_assets(
                    db,
                    api_url=get_setting_value(db, "plugin.proxmox_assets.api_url", ""),
                    token_id=get_setting_value(db, "plugin.proxmox_assets.token_id", ""),
                    token_secret=get_setting_value(db, "plugin.proxmox_assets.token_secret", ""),
                    verify_tls=get_setting_value(db, "plugin.proxmox_assets.verify_tls", "true") != "false",
                ),
                export_publishable_asset_updates(db),
            )[0],
        )
    except AssetActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=f"Asset action is already running: {exc.action}") from exc
    except Exception as exc:
        message = str(exc)
        diagnostic = db.query(Diagnostic).filter(Diagnostic.plugin == "proxmox_assets", Diagnostic.component == "plugin").first()
        if diagnostic is None:
            diagnostic = Diagnostic(plugin="proxmox_assets", component="plugin")
            db.add(diagnostic)
        diagnostic.status = "error"
        diagnostic.last_run = utc_now().replace(tzinfo=None)
        diagnostic.last_error = message
        db.commit()
        return RedirectResponse(url=f"/assets?{urlencode({'proxmox_error': message[:500]})}", status_code=303)
    return RedirectResponse(url="/assets", status_code=303)
