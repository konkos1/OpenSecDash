from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.models.events import Event
from app.plugins.base import DatasourcePlugin, PluginMetadata, PluginSetting, tail_text_file
from app.services.dashboard_metrics import metric_delta, today_counts, yesterday_counts
from app.services.events import normalize_event_time
from app.web.dashboard import DashboardWidget


logger = logging.getLogger(__name__)

# Cap per tick so a huge first-time backlog is drained over several ticks
# instead of one long blocking read; the manager also runs collect() in a
# worker thread, but a bounded batch keeps progress/commits incremental.
MAX_LINES_PER_TICK = 2000


class Plugin(DatasourcePlugin):
    metadata = PluginMetadata(
        id="geoblock_log", 
        name="GeoBlock Log", 
        version="1.0.0", 
        api_version="2",
        capabilities=["datasource", "widget"], 
        description="Reads GeoBlock Traefik plugin (github.com/PascalMinder/geoblock) logs."
    )
    settings = [
        PluginSetting("enabled", "geoblock_log.settings.enabled", "geoblock_log.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("log_path", "geoblock_log.settings.log_path", "geoblock_log.settings.log_path.help", "file", "/logs/geoblock.log"),
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
        self._offsets: dict[str, int] = {}
        self._inodes: dict[str, int] = {}
        self._sizes: dict[str, int] = {}

    async def health(self, context) -> dict[str, str]:
        path = Path(context.get("log_path"))
        if not path.exists():
            return {"status": "error", "message": f"GeoBlock log not found: {path}"}
        return {"status": "healthy", "message": f"GeoBlock log readable: {path}"}

    async def collect(self, context):
        path = Path(context.get("log_path"))
        if not path.exists():
            raise FileNotFoundError(path)
        assumed_tz = get_setting_value(context.db, "log_timestamp_timezone", "UTC")
        key = str(path)
        result = tail_text_file(
            path,
            self._offsets.get(key, 0),
            self._inodes.get(key),
            max_lines=MAX_LINES_PER_TICK,
            last_size=self._sizes.get(key),
        )
        self._offsets[key] = result.offset
        self._inodes[key] = result.inode
        self._sizes[key] = result.file_size
        progress_percent = int(result.offset / result.file_size * 100) if result.file_size else 100
        context.report_backlog(result.more_available, progress_percent)

        events = []
        for line in result.lines:
            parsed = self.parse_line(line, assumed_tz)
            if parsed:
                events.append(parsed)
        if events:
            logger.debug("Parsed %d GeoBlock log entries with events from %s", len(events), path)
        return events

    def parse_line(self, line: str, assumed_tz: str = "UTC"):
        if "request denied" not in line:
            return None
        ip_match = re.search(r"denied \[([^\]]+)\]", line)
        country_match = re.search(r"country \[([^\]]+)\]", line)
        time_match = re.search(r"GeoBlock:\s+(\d{4})/(\d{2})/(\d{2})\s+(\d{2}:\d{2}:\d{2})", line)
        event_time = None
        if time_match:
            # GeoBlock's own log line has no timezone offset (Go's default log
            # format) - it's the log writer's local wall-clock time, so the
            # configured `log_timestamp_timezone` setting decides how it maps
            # to UTC. Timestamps that already carry an explicit offset (e.g.
            # Traefik/CrowdSec logs) go through the tz-aware branch in
            # normalize_event_time() instead and ignore that setting entirely.
            event_time = f"{time_match.group(1)}-{time_match.group(2)}-{time_match.group(3)} {time_match.group(4)}"
        raw_data = line.strip()
        return {
            "event_time": normalize_event_time(event_time, assume_tz=assumed_tz),
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

    def ip_page_count_widgets(self, db: Session, ip: str) -> list[dict[str, Any]]:
        if get_setting_value(db, "plugin.geoblock_log.enabled", "false") != "true":
            return []
        return [
            {
                "key": "geoblocks",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type == "security.geoblock").count(),
                "href": f"/events?ip={ip}&event_type=security.geoblock",
            }
        ]

    def dashboard_widgets(self, db: Session) -> list[DashboardWidget]:
        if get_setting_value(db, "plugin.geoblock_log.enabled", "false") != "true":
            return []
        current = today_counts(db)
        previous = yesterday_counts(db)
        value = current.get("geoblocks", 0)
        return [
            DashboardWidget(
                id="geoblock_log.geoblocks_today",
                type="counter",
                section="security",
                title_key="dashboard.geoblocks_today",
                order=20,
                value=value,
                href="/events?event_type=security.geoblock&today=true",
                delta=metric_delta(value, previous.get("geoblocks")),
            )
        ]
