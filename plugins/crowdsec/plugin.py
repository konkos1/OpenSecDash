from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.models.events import Event
from app.plugins.base import ActionDefinition, ActionParameter, ActionPlugin, DatasourcePlugin, PeriodicPlugin, PluginMetadata, PluginSetting, tail_text_file
from app.services.dashboard_metrics import metric_delta, today_counts, yesterday_counts
from app.services.events import normalize_event_time
from app.web.dashboard import DashboardWidget

from .locales import LOCALES
from .services.decisions import active_decision_for_ip, crowdsec_lapi_status, sync_crowdsec_decisions
from .services.rollups import _top_daily_rollup_metric, _top_rollup_metric


logger = logging.getLogger(__name__)

# Cap per tick so a huge first-time backlog is drained over several ticks
# instead of one long blocking read; the manager also runs collect() in a
# worker thread, but a bounded batch keeps progress/commits incremental.
MAX_LINES_PER_TICK = 2000

BAN_ACTION_TYPES = frozenset({"security.ban", "crowdsec_ban"})
UNBAN_ACTION_TYPES = frozenset({"security.unban", "crowdsec_unban"})


class Plugin(DatasourcePlugin, PeriodicPlugin, ActionPlugin):
    action_definitions = (
        ActionDefinition(
            action_type="security.ban",
            aliases=frozenset({"crowdsec_ban"}),
            label_key="ip.crowdsec_ban",
            description_key="action.desc.security.ban",
            target_types=frozenset({"ip"}),
            critical=True,
            permission="security.ban",
            parameters=(
                ActionParameter(
                    name="duration",
                    kind="select",
                    options=("4h", "24h", "7d"),
                    default="4h",
                    label_key="action.param.duration",
                ),
            ),
        ),
        ActionDefinition(
            action_type="security.unban",
            aliases=frozenset({"crowdsec_unban"}),
            label_key="crowdsec.unban",
            description_key="action.desc.security.unban",
            target_types=frozenset({"ip"}),
            critical=True,
            permission="security.unban",
        ),
    )
    metadata = PluginMetadata(
        id="crowdsec",
        name="CrowdSec",
        version="1.0.0",
        api_version="2",
        capabilities=["datasource", "action", "page", "widget"],
        description="CrowdSec log datasource and LAPI ban/unban actions."
    )
    settings = [
        PluginSetting("enabled", "crowdsec.settings.enabled", "crowdsec.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("log_path", "crowdsec.settings.log_path", "crowdsec.settings.log_path.help", "file", "/logs/crowdsec.log"),
        PluginSetting("lapi_url", "crowdsec.settings.lapi_url", "crowdsec.settings.lapi_url.help", "url", "http://127.0.0.1:8080"),
        PluginSetting("lapi_login", "crowdsec.settings.lapi_login", "crowdsec.settings.lapi_login.help", "text", ""),
        PluginSetting("lapi_password", "crowdsec.settings.lapi_password", "crowdsec.settings.lapi_password.help", "password", ""),
        PluginSetting("poll_interval", "crowdsec.settings.poll_interval", "crowdsec.settings.poll_interval.help", "number", "10"),
    ]
    locales = LOCALES

    def __init__(self) -> None:
        self._offsets = {}; self._inodes = {}; self._sizes = {}
        self._decision_sync_counter = 0

    async def health(self, context) -> dict[str, str]:
        # This component reports the crowdsec.log datasource. LAPI health is
        # tracked independently by the decision-sync diagnostic below.
        log_path = Path(context.get("log_path"))
        if not log_path.exists():
            return {"status": "error", "message": f"CrowdSec log not found: {log_path}"}
        return {"status": "healthy", "message": f"CrowdSec log readable: {log_path}"}

    def refresh_diagnostics(self, db: Session) -> None:
        # Validate the credentials immediately when settings are saved so the
        # decision-sync diagnostic reflects the change before the next tick.
        sync_crowdsec_decisions(db, force=True)

    async def tick(self, context) -> None:
        self._decision_sync_counter += 1
        # The manager ticks roughly once per minute. Sync every second tick to
        # keep LAPI usage low while still keeping active bans reasonably fresh.
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

        from .services.lapi import lapi_add_ban, lapi_delete_decision, lapi_login

        url = context.get("lapi_url", "http://127.0.0.1:8080")
        logger.info("Executing CrowdSec action via LAPI type=%s target=%s", action_type, target)
        token = lapi_login(url, context.get("lapi_login", ""), context.get("lapi_password", ""))
        if is_ban:
            lapi_add_ban(url, token, target, parameters.get("duration", "4h"), parameters.get("reason", "OpenSecDash manual ban"))
            return {"status": "completed", "result": f"LAPI ban created for {target}"}
        lapi_delete_decision(url, token, decision_id)
        return {"status": "completed", "result": f"LAPI decision {decision_id} deleted"}

    # --- Action framework hooks (see app.plugins.base.ActionPlugin) ---

    def action_available(self, db: Session, action_type: str, target: str, dry_run: bool) -> bool:
        if not dry_run and get_setting_value(db, "plugin.crowdsec.enabled", "false") != "true":
            return False
        if action_type in UNBAN_ACTION_TYPES and not dry_run:
            return active_decision_for_ip(db, target) is not None
        return True

    def validate_action(self, db: Session, action_type: str, target: str, parameters: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        if not parameters.get("reason"):
            if action_type in BAN_ACTION_TYPES:
                parameters = {**parameters, "reason": "Manual ban via OpenSecDash"}
            elif action_type in UNBAN_ACTION_TYPES:
                parameters = {**parameters, "reason": "Manual unban via OpenSecDash"}
        # The action hook intentionally receives no target_type; action_type is
        # the routing contract. CrowdSec unban actions are IP-targeted actions,
        # so this validation is deliberately applied to every real unban action
        # of this type. If a caller supplied another target_type, the check is
        # stricter than the older target_type == "ip" guard rather than looser.
        # A real unban must target an actually-active decision, so its id can be
        # sent to CrowdSec. Skipped in dry-run (nothing is really executed).
        if action_type not in UNBAN_ACTION_TYPES or dry_run:
            return parameters
        decision = active_decision_for_ip(db, target)
        decision_id = str((parameters or {}).get("decision_id") or "").strip()
        if decision is None:
            raise ValueError("No active CrowdSec ban decision found for this IP")
        if not decision_id:
            return {**(parameters or {}), "decision_id": decision.decision_id}
        if decision_id != decision.decision_id:
            raise ValueError("CrowdSec decision id does not match the active ban for this IP")
        return parameters

    def prepare_parameters(self, db: Session, action: Any) -> dict[str, Any] | None:
        if action.action_type not in BAN_ACTION_TYPES or not action.parameters or not action.parameters.get("reason"):
            return None
        # action.id only exists after flush, so the id-tagged reason is
        # assembled here, then actually sent to CrowdSec as the ban reason (see
        # execute()) - this lets a later log-tailed re-import of CrowdSec's own
        # log line about this decision be correlated back to exactly this action
        # instead of guessing from timing (see events.find_duplicate_event).
        return {**action.parameters, "reason": f"{action.parameters['reason']} (action #{action.id})"}

    def success_event_type(self, action_type: str) -> str | None:
        if action_type in BAN_ACTION_TYPES:
            return "security.ban.manual"
        if action_type in UNBAN_ACTION_TYPES:
            return "security.unban.manual"
        return None

    def action_event_data(self, action: Any) -> dict[str, Any]:
        if action.action_type not in BAN_ACTION_TYPES:
            return {}
        # The CrowdSec page reads data_json.scenario/duration for every ban row
        # (manual or log-imported); without these a manual ban showed up with
        # neither, since "reason" is what's actually told to CrowdSec.
        parameters = action.parameters or {}
        return {"scenario": parameters.get("reason") or "Manual ban via OpenSecDash", "duration": parameters.get("duration")}

    def after_execute(self, db: Session, action: Any) -> None:
        sync_crowdsec_decisions(db, force=True)

    # --- Event dedupe rules (see app.services.events) ---

    def duplicate_rules(self):
        from .services.dedupe import RULES

        return RULES

    # --- Web surface: page, IP-explorer panel, nav (see app.plugins.web) ---

    def ip_page_context(self, db: Session, ip: str) -> dict[str, Any]:
        # The panel is included on the IP explorer for everyone (it also shows
        # in dry-run while the plugin is disabled), so guard the enabled-only
        # bits here. When the plugin is env-disabled it isn't loaded at all and
        # this hook never runs, so the panel disappears entirely.
        enabled = get_setting_value(db, "plugin.crowdsec.enabled", "false") == "true"
        return {
            "crowdsec_enabled": enabled,
            "active_decision": active_decision_for_ip(db, ip) if enabled else None,
            "lapi_status": crowdsec_lapi_status(db) if enabled else None,
        }

    def ip_page_count_widgets(self, db: Session, ip: str) -> list[dict[str, Any]]:
        if get_setting_value(db, "plugin.crowdsec.enabled", "false") != "true":
            return []
        return [
            {
                "key": "bans",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type.startswith("security.ban")).count(),
                "href": f"/events?ip={ip}&event_type=security.ban",
            }
        ]

    def dashboard_widgets(self, db: Session) -> list[DashboardWidget]:
        if get_setting_value(db, "plugin.crowdsec.enabled", "false") != "true":
            return []
        current = today_counts(db)
        previous = yesterday_counts(db)
        value = current.get("bans", 0)
        return [
            DashboardWidget(
                id="crowdsec.active_bans",
                type="counter",
                section="security",
                title_key="dashboard.active_bans",
                order=10,
                value=value,
                href="/events?event_type=security.ban*&today=true",
                delta=metric_delta(value, previous.get("bans")),
            ),
            DashboardWidget(
                id="crowdsec.top_scenarios",
                type="table",
                section="trends",
                title_key="crowdsec.dashboard_top_scenarios",
                order=20,
                rows=tuple(
                    {
                        "label": scenario or "unknown",
                        "value": count,
                        "href": f"/events?{urlencode({'event_type': 'security.ban*', 'q': scenario or 'unknown'})}",
                    }
                    for scenario, count in _top_daily_rollup_metric(db, "scenario", 10)
                ),
                empty_key="crowdsec.no_scenarios",
            ),
        ]

    def web(self):
        from pathlib import Path

        from app.plugins.web import PluginNavItem, PluginWebRegistration

        from .routes import router

        return PluginWebRegistration(
            router=router,
            templates_dir=Path(__file__).parent / "templates",
            nav_items=(PluginNavItem(label_key="nav.crowdsec", href="/crowdsec", active_prefix="/crowdsec"),),
            ip_page_panels=("crowdsec/ip_panel.html",),
        )
