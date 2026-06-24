from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.plugins.base import DatasourcePlugin, PluginMetadata, PluginSetting
from app.services.events import classify_access_status, normalize_event_time


logger = logging.getLogger(__name__)


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
        PluginSetting("log_path", "traefik_log.settings.log_path", "traefik_log.settings.log_path.help", "file", "/var/log/traefik/access.log"),
        PluginSetting("poll_interval", "traefik_log.settings.poll_interval", "traefik_log.settings.poll_interval.help", "number", "2"),
    ]
    locales = {
        "en": {
            "traefik_log.settings.enabled": "Traefik log enabled",
            "traefik_log.settings.enabled.help": "Continuously watches the Traefik JSON access log and imports new lines as access events.",
            "traefik_log.settings.log_path": "Traefik access log path",
            "traefik_log.settings.log_path.help": "Path to the Traefik JSON access.log. Fields are parsed like the proven traefik-logs.sh script.",
            "traefik_log.settings.poll_interval": "Traefik poll interval seconds",
            "traefik_log.settings.poll_interval.help": "How often the file is checked for appended lines and rotation.",
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
            "common.yes": "Ja",
            "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._offsets: dict[str, int] = {}
        self._inodes: dict[str, int] = {}

    async def health(self, context) -> dict[str, str]:
        path = Path(context.get("log_path"))
        if not path.exists():
            return {"status": "error", "message": f"Traefik access log not found: {path}"}
        return {"status": "healthy", "message": f"Traefik access log readable: {path}"}

    async def collect(self, context):
        path = Path(context.get("log_path"))
        if not path.exists():
            raise FileNotFoundError(path)
        stat = path.stat()
        key = str(path)
        offset = self._offsets.get(key, 0)
        if self._inodes.get(key) != stat.st_ino or stat.st_size < offset:
            offset = 0
            self._inodes[key] = stat.st_ino
        events = []
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(offset)
            for line in handle:
                parsed = self.parse_line(line)
                if parsed:
                    events.append(parsed)
            self._offsets[key] = handle.tell()
        if events:
            logger.debug("Parsed %d Traefik access log lines with events from %s", len(events), path)
        return events

    def parse_line(self, line: str) -> dict[str, Any] | None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        status = data.get("DownstreamStatus") or data.get("OriginStatus")
        status_code = int(status) if status not in (None, "") else None
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
