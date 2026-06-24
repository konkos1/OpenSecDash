from __future__ import annotations

import logging
import re
from pathlib import Path

from app.models.events import Event
from app.plugins.base import DatasourcePlugin, PluginMetadata, PluginSetting
from app.services.events import normalize_event_time


logger = logging.getLogger(__name__)


class Plugin(DatasourcePlugin):
    metadata = PluginMetadata(
        id="geoblock_log", 
        name="GeoBlock Log", 
        version="1.0.0", 
        capabilities=["datasource", "widget"], 
        description="Reads GeoBlock Traefik plugin (github.com/PascalMinder/geoblock) logs."
    )
    settings = [
        PluginSetting("enabled", "geoblock_log.settings.enabled", "geoblock_log.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("log_path", "geoblock_log.settings.log_path", "geoblock_log.settings.log_path.help", "file", "/var/log/traefik/geoblock.log"),
        PluginSetting("poll_interval", "geoblock_log.settings.poll_interval", "geoblock_log.settings.poll_interval.help", "number", "5"),
    ]
    locales = {
        "en": {
            "geoblock_log.settings.enabled": "GeoBlock log enabled",
            "geoblock_log.settings.enabled.help": "Watches the GeoBlock log and imports request denied lines as security.geoblock events.",
            "geoblock_log.settings.log_path": "GeoBlock log path",
            "geoblock_log.settings.log_path.help": "Path to geoblock.log. IP and country are extracted from the log file.",
            "geoblock_log.settings.poll_interval": "GeoBlock poll interval seconds",
            "geoblock_log.settings.poll_interval.help": "How often the log file is checked for new entries.",
            "common.yes": "Yes", "common.no": "No",
        },
        "de": {
            "geoblock_log.settings.enabled": "GeoBlock Log aktiviert",
            "geoblock_log.settings.enabled.help": "Überwacht das GeoBlock Log und importiert request-denied-Zeilen als security.geoblock Events.",
            "geoblock_log.settings.log_path": "GeoBlock Log-Pfad",
            "geoblock_log.settings.log_path.help": "Pfad zur geoblock.log. IP und Land werden aus dem Log entnommen.",
            "geoblock_log.settings.poll_interval": "GeoBlock Prüfintervall in Sekunden",
            "geoblock_log.settings.poll_interval.help": "Wie oft die Logdatei auf neue Einträge geprüft wird.",
            "common.yes": "Ja", "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._seen_raw: set[str] = set()
        self._seen_loaded = False

    async def health(self, context) -> dict[str, str]:
        path = Path(context.get("log_path"))
        if not path.exists():
            return {"status": "error", "message": f"GeoBlock log not found: {path}"}
        return {"status": "healthy", "message": f"GeoBlock log readable: {path}"}

    async def collect(self, context):
        path = Path(context.get("log_path"))
        if not path.exists():
            raise FileNotFoundError(path)
        self._load_seen_events(context)
        events = []
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                parsed = self.parse_line(line)
                if not parsed:
                    continue
                raw_data = parsed["raw_data"]
                if raw_data in self._seen_raw:
                    continue
                self._seen_raw.add(raw_data)
                events.append(parsed)
        if events:
            logger.debug("Parsed %d GeoBlock log entries with events from %s", len(events), path)
        return events

    def _load_seen_events(self, context) -> None:
        if self._seen_loaded:
            return
        rows = (
            context.db.query(Event.raw_data)
            .filter(Event.plugin == self.metadata.id, Event.raw_data.isnot(None))
            .all()
        )
        self._seen_raw.update(raw_data for (raw_data,) in rows if raw_data)
        self._seen_loaded = True
        logger.debug("Loaded %d previously seen GeoBlock raw events", len(self._seen_raw))

    def parse_line(self, line: str):
        if "request denied" not in line:
            return None
        ip_match = re.search(r"denied \[([^\]]+)\]", line)
        country_match = re.search(r"country \[([^\]]+)\]", line)
        time_match = re.search(r"GeoBlock:\s+(\d{4})/(\d{2})/(\d{2})\s+(\d{2}:\d{2}:\d{2})", line)
        event_time = None
        if time_match:
            event_time = f"{time_match.group(1)}-{time_match.group(2)}-{time_match.group(3)} {time_match.group(4)}"
        raw_data = line.strip()
        return {
            "event_time": normalize_event_time(event_time),
            "source": "GeoBlock Log",
            "source_id": "geoblock-log",
            "plugin": self.metadata.id,
            "plugin_id": self.metadata.id,
            "event_type": "security.geoblock",
            "severity": "warning",
            "ip": ip_match.group(1) if ip_match else None,
            "country": country_match.group(1).upper() if country_match else None,
            "data_json": {"message": raw_data},
            "raw_data": raw_data,
        }
