from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from app.plugins.base import ActionPlugin, DatasourcePlugin, PeriodicPlugin, PluginMetadata, PluginSetting, tail_text_file
from app.services.crowdsec_decisions import sync_crowdsec_decisions
from app.services.events import normalize_event_time


logger = logging.getLogger(__name__)

# Cap per tick so a huge first-time backlog is drained over several ticks
# instead of one long blocking read; the manager also runs collect() in a
# worker thread, but a bounded batch keeps progress/commits incremental.
MAX_LINES_PER_TICK = 2000


class Plugin(DatasourcePlugin, PeriodicPlugin, ActionPlugin):
    metadata = PluginMetadata(
        id="crowdsec", 
        name="CrowdSec", 
        version="1.0.0", 
        capabilities=["datasource", "action", "page", "widget"], 
        description="CrowdSec log datasource and cscli ban/unban actions."
    )
    settings = [
        PluginSetting("enabled", "crowdsec.settings.enabled", "crowdsec.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("log_path", "crowdsec.settings.log_path", "crowdsec.settings.log_path.help", "file", "/logs/crowdsec.log"),
        PluginSetting(
            "connection_mode",
            "crowdsec.settings.connection_mode",
            "crowdsec.settings.connection_mode.help",
            "select",
            "lapi",
            [("lapi", "crowdsec.settings.connection_mode.lapi"), ("cscli", "crowdsec.settings.connection_mode.cscli")],
        ),
        PluginSetting("lapi_url", "crowdsec.settings.lapi_url", "crowdsec.settings.lapi_url.help", "url", "http://127.0.0.1:8080", visible_if=("connection_mode", "lapi")),
        PluginSetting("lapi_login", "crowdsec.settings.lapi_login", "crowdsec.settings.lapi_login.help", "text", "", visible_if=("connection_mode", "lapi")),
        PluginSetting("lapi_password", "crowdsec.settings.lapi_password", "crowdsec.settings.lapi_password.help", "password", "", visible_if=("connection_mode", "lapi")),
        PluginSetting("cscli_path", "crowdsec.settings.cscli_path", "crowdsec.settings.cscli_path.help", "text", "/usr/local/bin/cscli", visible_if=("connection_mode", "cscli")),
        PluginSetting("poll_interval", "crowdsec.settings.poll_interval", "crowdsec.settings.poll_interval.help", "number", "10"),
    ]
    locales = {
        "en": {
            "crowdsec.settings.enabled": "CrowdSec plugin enabled",
            "crowdsec.settings.enabled.help": "Watches crowdsec.log for ban history and enables ban/unban actions and decision sync.",
            "crowdsec.settings.log_path": "CrowdSec log path",
            "crowdsec.settings.log_path.help": "Path to crowdsec.log. Ban history, scenarios and countries are derived from lines containing 'ban on Ip/Range' like security-report.sh.",
            "crowdsec.settings.connection_mode": "Connection to CrowdSec",
            "crowdsec.settings.connection_mode.help": "How decisions are synced and ban/unban actions are executed. Local API (recommended) talks to CrowdSec over HTTP with dedicated credentials and needs no cscli binary or config mounts. cscli runs the binary as a subprocess instead.",
            "crowdsec.settings.connection_mode.lapi": "Local API (recommended)",
            "crowdsec.settings.connection_mode.cscli": "cscli binary",
            "crowdsec.settings.lapi_url": "LAPI URL",
            "crowdsec.settings.lapi_url.help": "Base URL of the CrowdSec Local API, e.g. http://127.0.0.1:8080 with host networking.",
            "crowdsec.settings.lapi_login": "LAPI login",
            "crowdsec.settings.lapi_login.help": "Machine name registered for OpenSecDash. Create it on the CrowdSec host with: sudo cscli machines add opensecdash --auto -f /tmp/opensecdash-lapi.yaml",
            "crowdsec.settings.lapi_password": "LAPI password",
            "crowdsec.settings.lapi_password.help": "Password from the credentials file created by 'cscli machines add'. Stored encrypted.",
            "crowdsec.settings.cscli_path": "cscli path",
            "crowdsec.settings.cscli_path.help": "Command or absolute path used for active decisions and ban/unban actions.",
            "crowdsec.settings.poll_interval": "CrowdSec poll interval seconds",
            "crowdsec.settings.poll_interval.help": "How often crowdsec.log is checked for appended ban history entries.",
            "common.yes": "Yes", "common.no": "No",
        },
        "de": {
            "crowdsec.settings.enabled": "CrowdSec Plugin aktiviert",
            "crowdsec.settings.enabled.help": "Überwacht crowdsec.log für Ban-Historie und aktiviert Ban/Unban-Aktionen und Decision-Sync.",
            "crowdsec.settings.log_path": "CrowdSec Log-Pfad",
            "crowdsec.settings.log_path.help": "Pfad zur crowdsec.log. Ban-Historie, Szenarien und Länder werden wie in security-report.sh aus Zeilen mit 'ban on Ip/Range' abgeleitet.",
            "crowdsec.settings.connection_mode": "Verbindung zu CrowdSec",
            "crowdsec.settings.connection_mode.help": "Wie Decisions synchronisiert und Ban/Unban-Aktionen ausgeführt werden. Local API (empfohlen) spricht per HTTP mit eigenen Zugangsdaten mit CrowdSec und braucht weder cscli-Binary noch Config-Mounts. cscli führt stattdessen das Binary als Subprozess aus.",
            "crowdsec.settings.connection_mode.lapi": "Local API (empfohlen)",
            "crowdsec.settings.connection_mode.cscli": "cscli-Binary",
            "crowdsec.settings.lapi_url": "LAPI URL",
            "crowdsec.settings.lapi_url.help": "Basis-URL der CrowdSec Local API, z. B. http://127.0.0.1:8080 bei Host-Networking.",
            "crowdsec.settings.lapi_login": "LAPI Login",
            "crowdsec.settings.lapi_login.help": "Für OpenSecDash registrierter Machine-Name. Auf dem CrowdSec-Host anlegen mit: sudo cscli machines add opensecdash --auto -f /tmp/opensecdash-lapi.yaml",
            "crowdsec.settings.lapi_password": "LAPI Passwort",
            "crowdsec.settings.lapi_password.help": "Passwort aus der von 'cscli machines add' erzeugten Credentials-Datei. Wird verschlüsselt gespeichert.",
            "crowdsec.settings.cscli_path": "cscli Pfad",
            "crowdsec.settings.cscli_path.help": "Kommando oder absoluter Pfad für aktive Decisions und Ban/Unban-Aktionen.",
            "crowdsec.settings.poll_interval": "CrowdSec Prüfintervall in Sekunden",
            "crowdsec.settings.poll_interval.help": "Wie oft crowdsec.log auf neue Ban-Historien-Einträge geprüft wird.",
            "common.yes": "Ja", "common.no": "Nein",
        },
    }

    def __init__(self) -> None:
        self._offsets = {}; self._inodes = {}; self._sizes = {}
        self._decision_sync_counter = 0

    async def health(self, context) -> dict[str, str]:
        log_path = Path(context.get("log_path"))
        if not log_path.exists():
            return {"status": "error", "message": f"CrowdSec log not found: {log_path}"}
        if context.get("connection_mode", "lapi") == "lapi":
            from app.services.crowdsec_lapi import LapiError, lapi_login

            url = context.get("lapi_url", "http://127.0.0.1:8080")
            try:
                lapi_login(url, context.get("lapi_login", ""), context.get("lapi_password", ""))
            except LapiError as exc:
                return {"status": "error", "message": str(exc)}
            logger.debug("CrowdSec health OK: LAPI login at %s", url)
            return {"status": "healthy", "message": f"CrowdSec LAPI reachable and credentials accepted: {url}"}
        cscli = context.get("cscli_path", "/usr/local/bin/cscli")
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

    async def tick(self, context) -> None:
        self._decision_sync_counter += 1
        # The manager ticks roughly once per minute. Sync every second tick to
        # keep cscli usage low while still keeping active bans reasonably fresh.
        if self._decision_sync_counter % 2 == 1:
            return
        ok, message = sync_crowdsec_decisions(context.db)
        if not ok:
            raise RuntimeError(message)

    async def collect(self, context) -> list[dict[str, Any]]:
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
            parsed = self.parse_log_line(line)
            if parsed:
                events.append(parsed)
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
        is_ban = action_type in {"security.ban", "crowdsec_ban"}
        decision_id = str(parameters.get("decision_id") or "").strip()
        if not is_ban and not decision_id:
            raise RuntimeError("Missing active CrowdSec decision id for unban")

        if context.get("connection_mode", "lapi") == "lapi":
            from app.services.crowdsec_lapi import lapi_add_ban, lapi_delete_decision, lapi_login

            url = context.get("lapi_url", "http://127.0.0.1:8080")
            logger.info("Executing CrowdSec action via LAPI type=%s target=%s", action_type, target)
            token = lapi_login(url, context.get("lapi_login", ""), context.get("lapi_password", ""))
            if is_ban:
                lapi_add_ban(url, token, target, parameters.get("duration", "4h"), parameters.get("reason", "OpenSecDash manual ban"))
                return {"status": "completed", "result": f"LAPI ban created for {target}"}
            lapi_delete_decision(url, token, decision_id)
            return {"status": "completed", "result": f"LAPI decision {decision_id} deleted"}

        cscli = context.get("cscli_path", "/usr/local/bin/cscli")
        if is_ban:
            duration = parameters.get("duration", "4h")
            cmd = [cscli, "decisions", "add", "--ip", target, "--duration", duration, "--reason", parameters.get("reason", "OpenSecDash manual ban")]
        else:
            cmd = [cscli, "decisions", "delete", "--id", decision_id]
        logger.info("Executing CrowdSec action type=%s target=%s", action_type, target)
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout or "cscli failed")
        logger.info("CrowdSec action completed type=%s target=%s", action_type, target)
        return {"status": "completed", "result": completed.stdout.strip() or "cscli action completed"}
