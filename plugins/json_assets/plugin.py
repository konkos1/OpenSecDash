from __future__ import annotations

import logging
import time
from typing import Any

from app.models.assets import Asset
from app.core.logging import redact_sensitive
from app.plugins.base import PeriodicPlugin, PluginContext, PluginMetadata, PluginSetting
from app.services.json_assets_import import import_json_assets
from app.services.json_assets_source import load_asset_source


logger = logging.getLogger(__name__)


class Plugin(PeriodicPlugin):
    metadata = PluginMetadata(
        id="json_assets",
        name="JSON Assets",
        version="1.0.0",
        capabilities=["datasource", "page", "widget"],
        description="Imports assets from the assets.json inventory format.",
    )
    settings = [
        PluginSetting(
            "enabled",
            "json_assets.settings.enabled",
            "json_assets.settings.enabled.help",
            "boolean",
            "false",
            [("false", "common.no"), ("true", "common.yes")],
        ),
        PluginSetting(
            "source_type",
            "json_assets.settings.source_type",
            "json_assets.settings.source_type.help",
            "select",
            "file",
            [("file", "json_assets.option.file"), ("url", "json_assets.option.url")],
            visible_if=("enabled", "true"),
        ),
        PluginSetting(
            "source",
            "json_assets.settings.source",
            "json_assets.settings.source.help",
            "text",
            "/assets/assets.json",
            visible_if=("enabled", "true"),
        ),
        PluginSetting(
            "apps_master",
            "json_assets.settings.apps_master",
            "json_assets.settings.apps_master.help",
            "select",
            "opensecdash",
            [("opensecdash", "json_assets.option.master_opensecdash"), ("external", "json_assets.option.master_external")],
            visible_if=("enabled", "true"),
        ),
        PluginSetting(
            "inventory_interval",
            "json_assets.settings.inventory_interval",
            "json_assets.settings.inventory_interval.help",
            "number",
            "3600",
            visible_if=("enabled", "true"),
        ),
    ]
    locales = {
        "en": {
            "json_assets.settings.enabled": "JSON Assets plugin enabled",
            "json_assets.settings.enabled.help": "Shows asset navigation and dashboard widgets and enables imports from assets.json.",
            "json_assets.settings.source_type": "assets.json source type",
            "json_assets.settings.source_type.help": "Loads the assets.json inventory from a local file or HTTP URL.",
            "json_assets.settings.source": "assets.json source",
            "json_assets.settings.source.help": "Path or URL to assets.json. Missing apps are marked inactive and kept for history.",
            "json_assets.settings.apps_master": "Master for app values",
            "json_assets.settings.apps_master.help": "Controls whether version and release URL are maintained in OpenSecDash or overwritten from assets.json for existing apps.",
            "json_assets.option.master_opensecdash": "OpenSecDash",
            "json_assets.option.master_external": "External import",
            "json_assets.settings.inventory_interval": "assets.json update interval seconds",
            "json_assets.settings.inventory_interval.help": "How often the assets.json source is reloaded automatically. Use 0 to disable automatic reloads.",
            "json_assets.option.file": "File",
            "json_assets.option.url": "URL",
            "common.yes": "Yes",
            "common.no": "No",
        },
        "de": {
            "json_assets.settings.enabled": "JSON Assets Plugin aktiviert",
            "json_assets.settings.enabled.help": "Zeigt Asset-Navigation und Dashboard-Widgets und aktiviert JSON-Importe aus assets.json.",
            "json_assets.settings.source_type": "assets.json Quelltyp",
            "json_assets.settings.source_type.help": "Lädt die assets.json Asset-Daten aus einer lokalen Datei oder HTTP-URL.",
            "json_assets.settings.source": "assets.json Quelle",
            "json_assets.settings.source.help": "Pfad oder URL zu assets.json. Fehlende Apps werden inaktiv markiert und für Historie behalten.",
            "json_assets.settings.apps_master": "Master für App-Werte",
            "json_assets.settings.apps_master.help": "Legt fest, ob Version und Release-URL in OpenSecDash gepflegt oder bei bestehenden Apps aus assets.json überschrieben werden.",
            "json_assets.option.master_opensecdash": "OpenSecDash",
            "json_assets.option.master_external": "Externer Import",
            "json_assets.settings.inventory_interval": "assets.json Aktualisierungsintervall in Sekunden",
            "json_assets.settings.inventory_interval.help": "Wie oft die assets.json Quelle automatisch neu geladen wird. 0 deaktiviert automatische Importe.",
            "json_assets.option.file": "Datei",
            "json_assets.option.url": "URL",
            "common.yes": "Ja",
            "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._last_inventory_import = 0.0

    async def health(self, context: PluginContext) -> dict[str, str]:
        source = context.get("source", "/assets/assets.json")
        source_type = context.get("source_type", "file")
        if not source:
            return {"status": "error", "message": "assets.json source is not configured"}
        try:
            load_asset_source(source_type=source_type, source=source)
        except Exception as exc:
            return {"status": "error", "message": f"assets.json source not reachable: {exc}"}
        return {"status": "healthy", "message": "assets.json source reachable"}

    async def tick(self, context: PluginContext) -> None:
        now = time.monotonic()
        inventory_interval = self._interval(context.get("inventory_interval", "3600"))

        if inventory_interval > 0 and self._is_due(self._last_inventory_import, inventory_interval, now):
            result = self._import_inventory(context)
            logger.debug("JSON assets import completed: %s", result)
            self._last_inventory_import = now
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
        source = context.get("source", "/assets/assets.json")
        if not source:
            return {"systems_created": 0, "assets_created": 0, "assets_updated": 0, "assets_inactive": 0}
        logger.debug("Loading JSON assets source_type=%s source=%s", source_type, redact_sensitive(source))
        inventory: dict[str, Any] = load_asset_source(source_type=source_type, source=source)
        return import_json_assets(db=context.db, inventory=inventory)

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
        if not publishable_assets:
            logger.debug("Skipping asset export: no app has MQTT publishing enabled")
            return
        for asset in publishable_assets:
            await context.export_asset_update(asset)
