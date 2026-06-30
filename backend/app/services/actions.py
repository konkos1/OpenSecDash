from __future__ import annotations

import asyncio
import ipaddress
import logging
from threading import Lock

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.core import Action
from app.services.crowdsec_decisions import active_decision_for_ip, sync_crowdsec_decisions
from app.plugins.manager import get_plugin_manager
from app.services.events import store_event

logger = logging.getLogger(__name__)

CRITICAL_ACTIONS = {"security.ban", "security.unban", "crowdsec_ban", "crowdsec_unban"}
_ACTION_LOCK = Lock()
_RUNNING_ACTION_KEYS: set[tuple[str, str, str]] = set()


class ActionAlreadyRunning(ValueError):
    def __init__(self, action_type: str, target_type: str, target: str):
        self.action_type = action_type
        self.target_type = target_type
        self.target = target
        super().__init__(f"Action is already running: {action_type} {target_type} {target}")


def _action_key(action_type: str, target_type: str, target: str) -> tuple[str, str, str]:
    return (action_type, target_type, target)


def _acquire_action(action_type: str, target_type: str, target: str) -> tuple[str, str, str]:
    key = _action_key(action_type, target_type, target)
    with _ACTION_LOCK:
        if key in _RUNNING_ACTION_KEYS:
            raise ActionAlreadyRunning(action_type, target_type, target)
        _RUNNING_ACTION_KEYS.add(key)
    return key


def _release_action(key: tuple[str, str, str]) -> None:
    with _ACTION_LOCK:
        _RUNNING_ACTION_KEYS.discard(key)


def validate_ip_target(ip: str) -> None:
    target = ipaddress.ip_network(ip, strict=False) if "/" in ip else ipaddress.ip_address(ip)
    if not target.is_global:
        raise ValueError("Only global IP addresses or ranges are valid action targets")


def create_action(
    db: Session,
    action_type: str,
    target: str,
    target_type: str = "ip",
    parameters: dict | None = None,
    confirmed: bool = False,
) -> Action:
    requires_confirmation = action_type in CRITICAL_ACTIONS
    if requires_confirmation and not confirmed:
        raise ValueError("Action requires confirmation")
    if target_type == "ip" and action_type in CRITICAL_ACTIONS:
        validate_ip_target(target)
    dry_run = get_setting_value(db, "action_dry_run", "true").lower() == "true"
    if target_type == "ip" and action_type in {"security.unban", "crowdsec_unban"} and not dry_run:
        decision = active_decision_for_ip(db, target)
        decision_id = str((parameters or {}).get("decision_id") or "").strip()
        if decision is None:
            raise ValueError("No active CrowdSec ban decision found for this IP")
        if not decision_id:
            parameters = {**(parameters or {}), "decision_id": decision.decision_id}
        elif decision_id != decision.decision_id:
            raise ValueError("CrowdSec decision id does not match the active ban for this IP")

    action_key = _acquire_action(action_type, target_type, target)
    try:
        action = Action(
            timestamp=utc_now().replace(tzinfo=None),
            action_type=action_type,
            plugin_id="crowdsec" if action_type.startswith("security.") or action_type.startswith("crowdsec_") else "core",
            target_type=target_type,
            target=target,
            parameters=parameters or {},
            status="pending",
            requires_confirmation=requires_confirmation,
        )
        db.add(action)
        db.flush()
        logger.info("Created action id=%s type=%s target_type=%s target=%s", action.id, action.action_type, action.target_type, action.target)
        execute_action(db, action)
        db.commit()
        return action
    finally:
        _release_action(action_key)


def execute_action(db: Session, action: Action) -> None:
    dry_run = get_setting_value(db, "action_dry_run", "true").lower() == "true"
    action.status = "running"

    if dry_run:
        action.status = "completed"
        action.result = "dry-run: action was recorded but not executed"
        logger.info("Action id=%s completed in dry-run mode", action.id)
    else:
        try:
            result = asyncio.run(
                get_plugin_manager().execute_action(
                    db,
                    action.action_type,
                    action.target,
                    action.parameters or {},
                )
            )
            action.status = (result or {}).get("status", "completed")
            action.result = (result or {}).get("result", "action plugin execution completed")
            logger.info("Action id=%s finished with status=%s", action.id, action.status)
            if action.plugin_id == "crowdsec" and action.action_type in {"security.ban", "security.unban", "crowdsec_ban", "crowdsec_unban"}:
                sync_crowdsec_decisions(db, force=True)
        except Exception as exc:
            logger.exception("Action id=%s failed", action.id)
            action.status = "failed"
            action.result = str(exc)

    if action.action_type in {"security.ban", "crowdsec_ban"}:
        event_type = "security.ban.manual" if action.status == "completed" else "action.failed"
    elif action.action_type in {"security.unban", "crowdsec_unban"}:
        event_type = "security.unban.manual" if action.status == "completed" else "action.failed"
    else:
        event_type = "action.executed" if action.status == "completed" else "action.failed"
    store_event(
        db,
        source="Action Framework",
        source_id="actions",
        plugin="crowdsec" if action.action_type.startswith("security.") or action.action_type.startswith("crowdsec_") else "core",
        plugin_id="crowdsec" if action.action_type.startswith("security.") or action.action_type.startswith("crowdsec_") else "core",
        event_type=event_type,
        severity="info" if action.status == "completed" else "error",
        ip=action.target if action.target_type == "ip" else None,
        data_json={
            "action_id": action.id,
            "action_type": action.action_type,
            "target_type": action.target_type,
            "target": action.target,
            "status": action.status,
            "result": action.result,
            "manual": True,
            "trigger": "manual",
        },
    )
