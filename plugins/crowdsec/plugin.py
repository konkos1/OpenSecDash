from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from app.plugins.base import ActionPlugin, DatasourcePlugin, PluginMetadata, PluginSetting
from app.services.events import normalize_event_time


logger = logging.getLogger(__name__)


class Plugin(DatasourcePlugin, ActionPlugin):
    metadata = PluginMetadata(
        id="crowdsec", 
        name="CrowdSec", 
        version="1.0.0", 
        capabilities=["datasource", "action", "page", "widget"], 
        description="CrowdSec log datasource and cscli ban/unban actions."
    )
    settings = [
        PluginSetting("enabled", "crowdsec.settings.enabled", "crowdsec.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("log_path", "crowdsec.settings.log_path", "crowdsec.settings.log_path.help", "file", "/var/log/crowdsec/crowdsec.log"),
        PluginSetting("cscli_path", "crowdsec.settings.cscli_path", "crowdsec.settings.cscli_path.help", "text", "cscli"),
        PluginSetting("poll_interval", "crowdsec.settings.poll_interval", "crowdsec.settings.poll_interval.help", "number", "10"),
    ]
    locales = {
        "en": {
            "crowdsec.settings.enabled": "CrowdSec plugin enabled",
            "crowdsec.settings.enabled.help": "Watches crowdsec.log for ban history and enables cscli actions for ban/unban.",
            "crowdsec.settings.log_path": "CrowdSec log path",
            "crowdsec.settings.log_path.help": "Path to crowdsec.log. Ban history, scenarios and countries are derived from lines containing 'ban on Ip/Range' like security-report.sh.",
            "crowdsec.settings.cscli_path": "cscli path",
            "crowdsec.settings.cscli_path.help": "Command or absolute path used for active decisions and ban/unban actions.",
            "crowdsec.settings.poll_interval": "CrowdSec poll interval seconds",
            "crowdsec.settings.poll_interval.help": "How often crowdsec.log is checked for appended ban history entries.",
            "common.yes": "Yes", "common.no": "No",
        },
        "de": {
            "crowdsec.settings.enabled": "CrowdSec Plugin aktiviert",
            "crowdsec.settings.enabled.help": "Überwacht crowdsec.log für Ban-Historie und aktiviert cscli-Aktionen für Ban/Unban.",
            "crowdsec.settings.log_path": "CrowdSec Log-Pfad",
            "crowdsec.settings.log_path.help": "Pfad zur crowdsec.log. Ban-Historie, Szenarien und Länder werden wie in security-report.sh aus Zeilen mit 'ban on Ip/Range' abgeleitet.",
            "crowdsec.settings.cscli_path": "cscli Pfad",
            "crowdsec.settings.cscli_path.help": "Kommando oder absoluter Pfad für aktive Decisions und Ban/Unban-Aktionen.",
            "crowdsec.settings.poll_interval": "CrowdSec Prüfintervall in Sekunden",
            "crowdsec.settings.poll_interval.help": "Wie oft crowdsec.log auf neue Ban-Historien-Einträge geprüft wird.",
            "common.yes": "Ja", "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._offsets = {}; self._inodes = {}

    async def health(self, context) -> dict[str, str]:
        log_path = Path(context.get("log_path"))
        if not log_path.exists():
            return {"status": "error", "message": f"CrowdSec log not found: {log_path}"}
        cscli = context.get("cscli_path", "cscli")
        try:
            completed = subprocess.run([cscli, "version"], capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            return {"status": "error", "message": f"cscli not found: {cscli}"}
        except Exception as exc:
            return {"status": "error", "message": f"cscli check failed: {exc}"}
        if completed.returncode != 0:
            return {"status": "error", "message": (completed.stderr or completed.stdout or "cscli version failed").strip()}
        version = (completed.stdout or completed.stderr or "cscli reachable").strip().splitlines()[0]
        logger.debug("CrowdSec health OK: %s", version)
        return {"status": "healthy", "message": f"cscli reachable: {version}"}

    async def collect(self, context) -> list[dict[str, Any]]:
        events = []
        path = Path(context.get("log_path"))
        if path.exists():
            stat = path.stat(); key = str(path); offset = self._offsets.get(key, 0)
            if self._inodes.get(key) != stat.st_ino or stat.st_size < offset:
                offset = 0; self._inodes[key] = stat.st_ino
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                handle.seek(offset)
                for line in handle:
                    parsed = self.parse_log_line(line)
                    if parsed:
                        events.append(parsed)
                self._offsets[key] = handle.tell()
        else:
            raise FileNotFoundError(path)
        if events:
            logger.debug("Parsed %d CrowdSec ban log entries with events from %s", len(events), path)
        return events

    def parse_log_line(self, line: str) -> dict[str, Any] | None:
        if not re.search(r"\bban\b", line, re.IGNORECASE):
            return None
        ip_match = re.search(r"(?:on\s+(?:ip|range)|by\s+(?:ip|range))\s+([^\"\s(]+)", line, re.IGNORECASE)
        scenario_match = re.search(
            r'scenario="([^"]+)"|\)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s+by\s+(?:ip|range)|\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s+by\s+(?:ip|range)',
            line,
            re.IGNORECASE,
        )
        time_match = re.search(r'time="([^"]+)"', line)
        duration_match = re.search(r"\b(\d+)\s*([smhdw])\s+ban\b", line, re.IGNORECASE)
        country_match = re.search(r"\(([A-Z]{2})/\d+\)", line)
        scenario = None
        if scenario_match:
            scenario = scenario_match.group(1) or scenario_match.group(2) or scenario_match.group(3)
        duration = None
        if duration_match:
            duration = f"{duration_match.group(1)}{duration_match.group(2).lower()}"
        return {
            "event_time": normalize_event_time(time_match.group(1) if time_match else None),
            "source": "CrowdSec Log",
            "source_id": "crowdsec-log",
            "plugin": self.metadata.id,
            "plugin_id": self.metadata.id,
            "event_type": "security.ban",
            "severity": "warning",
            "ip": ip_match.group(1) if ip_match else None,
            "country": country_match.group(1).upper() if country_match else None,
            "data_json": {"scenario": scenario, "duration": duration, "message": line.strip()},
            "raw_data": line.strip(),
        }

    async def execute(self, context, action_type: str, target: str, parameters: dict[str, Any]) -> dict[str, Any] | None:
        if action_type not in {"security.ban", "security.unban", "crowdsec_ban", "crowdsec_unban"}:
            return None
        cscli = context.get("cscli_path", "cscli")
        if action_type in {"security.ban", "crowdsec_ban"}:
            duration = parameters.get("duration", "4h")
            cmd = [cscli, "decisions", "add", "--ip", target, "--duration", duration, "--reason", parameters.get("reason", "OpenSecDash manual ban")]
        else:
            cmd = [cscli, "decisions", "delete", "--ip", target]
        logger.info("Executing CrowdSec action type=%s target=%s", action_type, target)
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout or "cscli failed")
        logger.info("CrowdSec action completed type=%s target=%s", action_type, target)
        return {"status": "completed", "result": completed.stdout.strip() or "cscli action completed"}
