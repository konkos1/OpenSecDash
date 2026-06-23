from __future__ import annotations

import logging
import time
from typing import Any

from app.models.assets import Asset
from app.core.logging import redact_sensitive
from app.plugins.base import PeriodicPlugin, PluginContext, PluginMetadata, PluginSetting
from app.services.apps_inventory_import import import_apps_inventory
from app.services.apps_inventory_source import load_asset_source
from app.services.apps_inventory_updates import refresh_asset_updates


logger = logging.getLogger(__name__)


class Plugin(PeriodicPlugin):
    metadata = PluginMetadata(
        id="apps_inventory",
        name="Apps Inventory (apps-installed.json)",
        version="1.0.0",
        capabilities=["datasource", "page", "widget"],
        description="Imports assets from the apps-installed.json inventory format.",
    )
    settings = [
        PluginSetting(
            "enabled",
            "apps_inventory.settings.enabled",
            "apps_inventory.settings.enabled.help",
            "boolean",
            "false",
            [("false", "common.no"), ("true", "common.yes")],
        ),
        PluginSetting(
            "source_type",
            "apps_inventory.settings.source_type",
            "apps_inventory.settings.source_type.help",
            "select",
            "file",
            [("file", "apps_inventory.option.file"), ("url", "apps_inventory.option.url")],
            visible_if=("enabled", "true"),
        ),
        PluginSetting(
            "source",
            "apps_inventory.settings.source",
            "apps_inventory.settings.source.help",
            "text",
            "dev-data/apps-installed.json",
            visible_if=("enabled", "true"),
        ),
        PluginSetting(
            "inventory_interval",
            "apps_inventory.settings.inventory_interval",
            "apps_inventory.settings.inventory_interval.help",
            "number",
            "3600",
            visible_if=("enabled", "true"),
        ),
        PluginSetting(
            "github_token",
            "apps_inventory.settings.github_token",
            "apps_inventory.settings.github_token.help",
            "password",
            "",
            visible_if=("enabled", "true"),
        ),
        PluginSetting(
            "github_interval",
            "apps_inventory.settings.github_interval",
            "apps_inventory.settings.github_interval.help",
            "number",
            "21600",
            visible_if=("enabled", "true"),
        ),
    ]
    locales = {
        "en": {
            "apps_inventory.settings.enabled": "Apps Inventory plugin enabled",
            "apps_inventory.settings.enabled.help": "Shows asset navigation and dashboard widgets and enables imports from apps-installed.json.",
            "apps_inventory.settings.source_type": "apps-installed.json source type",
            "apps_inventory.settings.source_type.help": "Loads the apps-installed.json inventory from a local file or HTTP URL.",
            "apps_inventory.settings.source": "apps-installed.json source",
            "apps_inventory.settings.source.help": "Path or URL to apps-installed.json. Missing apps are marked inactive and kept for history.",
            "apps_inventory.settings.inventory_interval": "apps-installed.json update interval seconds",
            "apps_inventory.settings.inventory_interval.help": "How often the apps-installed.json source is reloaded automatically. Use 0 to disable automatic reloads.",
            "apps_inventory.settings.github_token": "GitHub API token",
            "apps_inventory.settings.github_token.help": "Optional token used for release checks to avoid GitHub rate limits.",
            "apps_inventory.settings.github_interval": "GitHub release check interval seconds",
            "apps_inventory.settings.github_interval.help": "How often GitHub releases are checked for installed apps. Use 0 to disable automatic checks.",
            "apps_inventory.option.file": "File",
            "apps_inventory.option.url": "URL",
            "common.yes": "Yes",
            "common.no": "No",
        },
        "de": {
            "apps_inventory.settings.enabled": "Apps-Inventory Plugin aktiviert",
            "apps_inventory.settings.enabled.help": "Zeigt Asset-Navigation und Dashboard-Widgets und aktiviert Importe aus apps-installed.json.",
            "apps_inventory.settings.source_type": "apps-installed.json Quelltyp",
            "apps_inventory.settings.source_type.help": "Lädt das apps-installed.json Inventar aus einer lokalen Datei oder HTTP-URL.",
            "apps_inventory.settings.source": "apps-installed.json Quelle",
            "apps_inventory.settings.source.help": "Pfad oder URL zu apps-installed.json. Fehlende Apps werden inaktiv markiert und für Historie behalten.",
            "apps_inventory.settings.inventory_interval": "apps-installed.json Aktualisierungsintervall in Sekunden",
            "apps_inventory.settings.inventory_interval.help": "Wie oft die apps-installed.json Quelle automatisch neu geladen wird. 0 deaktiviert automatische Importe.",
            "apps_inventory.settings.github_token": "GitHub API Token",
            "apps_inventory.settings.github_token.help": "Optionaler Token für Release-Prüfungen, um GitHub Rate Limits zu vermeiden.",
            "apps_inventory.settings.github_interval": "GitHub Release-Prüfintervall in Sekunden",
            "apps_inventory.settings.github_interval.help": "Wie oft GitHub Releases für installierte Apps geprüft werden. 0 deaktiviert automatische Prüfungen.",
            "apps_inventory.option.file": "Datei",
            "apps_inventory.option.url": "URL",
            "common.yes": "Ja",
            "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._last_inventory_import = 0.0
        self._last_github_check = 0.0

    async def health(self, context: PluginContext) -> dict[str, str]:
        source = context.get("source", "dev-data/apps-installed.json")
        source_type = context.get("source_type", "file")
        if not source:
            return {"status": "error", "message": "apps-installed.json source is not configured"}
        try:
            load_asset_source(source_type=source_type, source=source)
        except Exception as exc:
            return {"status": "error", "message": f"apps-installed.json source not reachable: {exc}"}
        return {"status": "healthy", "message": "apps-installed.json source reachable"}

    async def tick(self, context: PluginContext) -> None:
        now = time.monotonic()
        inventory_interval = self._interval(context.get("inventory_interval", "3600"))
        github_interval = self._interval(context.get("github_interval", "21600"))

        if inventory_interval > 0 and self._is_due(self._last_inventory_import, inventory_interval, now):
            result = self._import_inventory(context)
            logger.info("Apps inventory import completed: %s", result)
            self._last_inventory_import = now
            await self._export_assets(context)

        if github_interval > 0 and self._is_due(self._last_github_check, github_interval, now):
            refresh_asset_updates(context.db)
            logger.info("Apps inventory GitHub release check completed")
            self._last_github_check = now
            await self._export_assets(context)

    @staticmethod
    def _interval(value: str) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_due(last_run: float, interval: int, now: float) -> bool:
        return last_run == 0 or now - last_run >= interval

    def _import_inventory(self, context: PluginContext) -> dict[str, int]:
        source_type = context.get("source_type", "file")
        source = context.get("source", "dev-data/apps-installed.json")
        if not source:
            return {"systems_created": 0, "assets_created": 0, "assets_updated": 0, "assets_inactive": 0}
        logger.info("Loading apps inventory source_type=%s source=%s", source_type, redact_sensitive(source))
        inventory: dict[str, Any] = load_asset_source(source_type=source_type, source=source)
        return import_apps_inventory(db=context.db, inventory=inventory)

    async def _export_assets(self, context: PluginContext) -> None:
        for asset in context.db.query(Asset).all():
            await context.export_asset_update(asset)
