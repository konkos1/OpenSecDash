from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.models.events import Event
from app.plugins.base import DatasourcePlugin, PluginMetadata, PluginSetting, tail_text_file
from app.services.events import classify_access_status, normalize_event_time


logger = logging.getLogger(__name__)

# Cap per tick so a huge first-time backlog is drained over several ticks
# instead of one long blocking read; the manager also runs collect() in a
# worker thread, but a bounded batch keeps progress/commits incremental.
MAX_LINES_PER_TICK = 2000


class Plugin(DatasourcePlugin):
    metadata = PluginMetadata(
        id="traefik_log",
        name="Traefik Access Log",
        version="1.0.0",
        capabilities=["datasource", "page", "widget"],
        description="Reads Traefik JSON access logs and emits access.* events.",
    )
    settings = [
        PluginSetting("enabled", "traefik_log.settings.enabled", "traefik_log.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("log_path", "traefik_log.settings.log_path", "traefik_log.settings.log_path.help", "file", "/logs/access.log"),
        PluginSetting("poll_interval", "traefik_log.settings.poll_interval", "traefik_log.settings.poll_interval.help", "number", "2"),
        PluginSetting("hide_local_ips_default", "traefik_log.settings.hide_local_ips_default", "traefik_log.settings.hide_local_ips_default.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
    ]
    locales = {
        "en": {
            "traefik_log.settings.enabled": "Traefik log enabled",
            "traefik_log.settings.enabled.help": "Continuously watches the Traefik JSON access log and imports new lines as access events.",
            "traefik_log.settings.log_path": "Traefik access log path",
            "traefik_log.settings.log_path.help": "Path to the Traefik JSON access.log. Fields are parsed like the proven traefik-logs.sh script.",
            "traefik_log.settings.poll_interval": "Traefik poll interval seconds",
            "traefik_log.settings.poll_interval.help": "How often the file is checked for appended lines and rotation.",
            "traefik_log.settings.hide_local_ips_default": "Hide local IPs by default",
            "traefik_log.settings.hide_local_ips_default.help": "Starts the Access page with local/private IPs hidden unless the filter is changed manually.",
            "common.yes": "Yes",
            "common.no": "No",
        },
        "de": {
            "traefik_log.settings.enabled": "Traefik Log aktiviert",
            "traefik_log.settings.enabled.help": "Überwacht fortlaufend das Traefik JSON access.log und importiert neue Zeilen als Access-Events.",
            "traefik_log.settings.log_path": "Traefik Access-Log-Pfad",
            "traefik_log.settings.log_path.help": "Pfad zum Traefik JSON access.log. Die Felder werden wie im erprobten traefik-logs.sh geparst.",
            "traefik_log.settings.poll_interval": "Traefik Prüfintervall in Sekunden",
            "traefik_log.settings.poll_interval.help": "Wie oft die Datei auf neue Zeilen und Rotation geprüft wird.",
            "traefik_log.settings.hide_local_ips_default": "Lokale IPs standardmäßig ausblenden",
            "traefik_log.settings.hide_local_ips_default.help": "Öffnet die Access-Seite standardmäßig mit ausgeblendeten lokalen/privaten IPs, solange der Filter nicht manuell geändert wird.",
            "common.yes": "Ja",
            "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._offsets: dict[str, int] = {}
        self._inodes: dict[str, int] = {}
        self._sizes: dict[str, int] = {}

    async def health(self, context) -> dict[str, str]:
        path = Path(context.get("log_path"))
        if not path.exists():
            return {"status": "error", "message": f"Traefik access log not found: {path}"}
        return {"status": "healthy", "message": f"Traefik access log readable: {path}"}

    async def collect(self, context):
        path = Path(context.get("log_path"))
        if not path.exists():
            raise FileNotFoundError(path)
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
            parsed = self.parse_line(line)
            if parsed:
                events.append(parsed)
        if events:
            logger.debug("Parsed %d Traefik access log lines with events from %s", len(events), path)
        return events

    def parse_line(self, line: str) -> dict[str, Any] | None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        status = data.get("DownstreamStatus") or data.get("OriginStatus")
        try:
            status_code = int(status) if status not in (None, "") else None
        except (TypeError, ValueError):
            # A single log line with a non-numeric status must not abort the
            # whole batch - the file offset has already moved past it.
            status_code = None
        event_type, severity = classify_access_status(status_code)
        return {
            "event_time": normalize_event_time(data.get("StartUTC") or data.get("StartLocal") or data.get("time")),
            "source": "Traefik Access Log",
            "source_id": "traefik-access-log",
            "plugin": self.metadata.id,
            "plugin_id": self.metadata.id,
            "event_type": event_type,
            "severity": severity,
            "ip": data.get("ClientHost"),
            "hostname": data.get("RequestHost") or data.get("RequestAddr"),
            "method": data.get("RequestMethod"),
            "path": data.get("RequestPath"),
            "status_code": status_code,
            "data_json": {
                "resource": f"{data.get('RequestHost', '-')}{data.get('RequestPath', '')}",
                "request_scheme": data.get("RequestScheme"),
                "request_url": f"{data.get('RequestScheme', 'https')}://{data.get('RequestHost') or data.get('RequestAddr')}{data.get('RequestPath', '')}" if data.get("RequestHost") or data.get("RequestAddr") else None,
                "content_type": data.get("downstream_Content-Type") or data.get("origin_Content-Type"),
                "user_agent": data.get("request_User-Agent"),
                "router_name": data.get("RouterName"),
                "service_name": data.get("ServiceName"),
            },
            "raw_data": line.strip(),
        }

    def ip_page_count_widgets(self, db: Session, ip: str) -> list[dict[str, Any]]:
        if get_setting_value(db, "plugin.traefik_log.enabled", "false") != "true":
            return []
        return [
            {
                "key": "access",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type.startswith("access.")).count(),
                "href": f"/events?ip={ip}&event_type=access.*",
            }
        ]
