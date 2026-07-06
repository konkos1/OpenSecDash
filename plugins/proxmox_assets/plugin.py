from __future__ import annotations

import logging

from app.models.assets import Asset
from app.plugins.base import PeriodicPlugin, PluginContext, PluginMetadata, PluginSetting
from app.services.proxmox_assets import ProxmoxClient, inspect_proxmox_guest_visibility, proxmox_visibility_message, sync_proxmox_assets

logger = logging.getLogger(__name__)


class Plugin(PeriodicPlugin):
    metadata = PluginMetadata(
        id="proxmox_assets",
        name="Proxmox Assets",
        version="1.0.0",
        capabilities=["asset_source", "widget"],
        description="Imports Proxmox nodes, VMs/LXCs, and optional app metadata from hidden notes blocks.",
    )
    settings = [
        PluginSetting("enabled", "proxmox.settings.enabled", "proxmox.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("api_url", "proxmox.settings.api_url", "proxmox.settings.api_url.help", "url", "https://pve.local:8006"),
        PluginSetting("token_id", "proxmox.settings.token_id", "proxmox.settings.token_id.help", "text", ""),
        PluginSetting("token_secret", "proxmox.settings.token_secret", "proxmox.settings.token_secret.help", "password", ""),
        PluginSetting("verify_tls", "proxmox.settings.verify_tls", "proxmox.settings.verify_tls.help", "boolean", "true", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("poll_interval", "proxmox.settings.poll_interval", "proxmox.settings.poll_interval.help", "number", "300"),
    ]
    locales = {
        "en": {
            "proxmox.settings.enabled": "Proxmox Assets enabled",
            "proxmox.settings.enabled.help": "Imports Proxmox nodes and guests. Apps can be declared in hidden HTML comment blocks in Proxmox notes.",
            "proxmox.settings.api_url": "Proxmox API URL",
            "proxmox.settings.api_url.help": "Example: https://pve.example.local:8006",
            "proxmox.settings.token_id": "API token ID",
            "proxmox.settings.token_id.help": "Example: opensecdash@pve!inventory",
            "proxmox.settings.token_secret": "API token secret",
            "proxmox.settings.token_secret.help": "Secret value for the Proxmox API token.",
            "proxmox.settings.verify_tls": "Verify TLS certificate",
            "proxmox.settings.verify_tls.help": "Disable only for self-signed homelab certificates you trust.",
            "proxmox.settings.poll_interval": "Poll interval seconds",
            "proxmox.settings.poll_interval.help": "How often Proxmox assets are synchronized. Default: 300 seconds.",
            "common.yes": "Yes", "common.no": "No",
        },
        "de": {
            "proxmox.settings.enabled": "Proxmox Assets aktiviert",
            "proxmox.settings.enabled.help": "Importiert Proxmox-Nodes und Guests. Apps können in versteckten HTML-Kommentaren in Proxmox Notes deklariert werden.",
            "proxmox.settings.api_url": "Proxmox API-URL",
            "proxmox.settings.api_url.help": "Beispiel: https://pve.example.local:8006",
            "proxmox.settings.token_id": "API-Token-ID",
            "proxmox.settings.token_id.help": "Beispiel: opensecdash@pve!inventory",
            "proxmox.settings.token_secret": "API-Token-Secret",
            "proxmox.settings.token_secret.help": "Secret-Wert des Proxmox API-Tokens.",
            "proxmox.settings.verify_tls": "TLS-Zertifikat prüfen",
            "proxmox.settings.verify_tls.help": "Nur für vertrauenswürdige selbstsignierte Homelab-Zertifikate deaktivieren.",
            "proxmox.settings.poll_interval": "Prüfintervall in Sekunden",
            "proxmox.settings.poll_interval.help": "Wie oft Proxmox-Assets synchronisiert werden. Standard: 300 Sekunden.",
            "common.yes": "Ja", "common.no": "Nein",
        },
    }

    async def health(self, context) -> dict[str, str]:
        if not context.get("api_url") or not context.get("token_id") or not context.get("token_secret"):
            return {"status": "warning", "message": "Proxmox API URL or token is missing."}
        try:
            client = ProxmoxClient(context.get("api_url"), context.get("token_id"), context.get("token_secret"), verify_tls=context.get("verify_tls", "true") == "true")
            nodes = [item for item in client.get("/nodes") if isinstance(item, dict)]
            visibility = inspect_proxmox_guest_visibility(client, nodes)
            guests = visibility.get("guests") if isinstance(visibility.get("guests"), list) else []
            detail = proxmox_visibility_message(visibility)
            if not guests:
                return {"status": "warning", "message": f"Proxmox reachable, but no VM/LXC guests visible. {detail}. Check VM.Audit permissions with propagation on the token if this is unexpected."}
            return {"status": "healthy", "message": f"Proxmox reachable; {len(guests)} guest(s) visible. {detail}."}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def tick(self, context: PluginContext) -> None:
        # Take the same lock as the manual "Proxmox sync" button so the
        # periodic sync can never interleave with a user-triggered one - both
        # write the same system/asset rows. If an asset action is busy right
        # now, skip; the next tick retries.
        from app.services.asset_actions import AssetActionAlreadyRunning, run_asset_action

        try:
            run_asset_action(
                "proxmox_sync",
                lambda: sync_proxmox_assets(
                    context.db,
                    api_url=context.get("api_url"),
                    token_id=context.get("token_id"),
                    token_secret=context.get("token_secret"),
                    verify_tls=context.get("verify_tls", "true") == "true",
                ),
            )
        except AssetActionAlreadyRunning:
            logger.debug("Skipping periodic Proxmox sync: an asset action is already running")
            return
        await self._export_assets(context)

    async def _export_assets(self, context: PluginContext) -> None:
        publishable_assets = (
            context.db.query(Asset)
            .filter(
                Asset.mqtt_publish_enabled == True,
                Asset.version.isnot(None),
                Asset.latest_version.isnot(None),
                Asset.release_url.isnot(None),
            )
            .all()
        )
        for asset in publishable_assets:
            await context.export_asset_update(asset)
