from __future__ import annotations

import asyncio
import ipaddress
import logging
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.core import Action
from app.plugins.manager import get_plugin_manager
from app.services.events import store_event

logger = logging.getLogger(__name__)

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
    manager = get_plugin_manager()
    action_plugin = manager.action_plugin_for(action_type)
    critical = manager.critical_action_types()
    requires_confirmation = action_type in critical
    if requires_confirmation and not confirmed:
        raise ValueError("Action requires confirmation")
    if target_type == "ip" and action_type in critical:
        validate_ip_target(target)
    dry_run = get_setting_value(db, "action_dry_run", "true").lower() == "true"
    # Plugin-specific validation/normalization (e.g. CrowdSec checks an unban
    # against an active decision and fills in its id). May raise ValueError.
    if action_plugin is not None:
        parameters = action_plugin.validate_action(db, action_type, target, parameters or {}, dry_run)

    action_key = _acquire_action(action_type, target_type, target)
    try:
        action = Action(
            timestamp=utc_now().replace(tzinfo=None),
            action_type=action_type,
            plugin_id=manager.plugin_id_for_action(action_type),
            target_type=target_type,
            target=target,
            parameters=parameters or {},
            status="pending",
            requires_confirmation=requires_confirmation,
        )
        db.add(action)
        db.flush()
        logger.info("Created action id=%s type=%s target_type=%s target=%s", action.id, action.action_type, action.target_type, action.target)
        # The id only exists after flush; a plugin may fold it into the params
        # actually sent out (e.g. CrowdSec embeds it into the ban reason so a
        # later log re-import can be correlated back to this exact action).
        if action_plugin is not None:
            new_parameters = action_plugin.prepare_parameters(db, action)
            if new_parameters is not None:
                action.parameters = new_parameters  # reassignment: plain JSON column, no MutableDict
        execute_action(db, action)
        db.commit()
        return action
    finally:
        _release_action(action_key)


def execute_action(db: Session, action: Action) -> None:
    manager = get_plugin_manager()
    action_plugin = manager.action_plugin_for(action.action_type)
    dry_run = get_setting_value(db, "action_dry_run", "true").lower() == "true"
    action.status = "running"

    if dry_run:
        action.status = "completed"
        action.result = "dry-run: action was recorded but not executed"
        logger.info("Action id=%s completed in dry-run mode", action.id)
    else:
        try:
            result = asyncio.run(
                manager.execute_action(
                    db,
                    action.action_type,
                    action.target,
                    action.parameters or {},
                )
            )
            action.status = (result or {}).get("status", "completed")
            action.result = (result or {}).get("result", "action plugin execution completed")
            logger.info("Action id=%s finished with status=%s", action.id, action.status)
            # e.g. CrowdSec re-syncs its active decisions after a real ban/unban.
            if action.status == "completed" and action_plugin is not None:
                action_plugin.after_execute(db, action)
        except Exception as exc:
            logger.exception("Action id=%s failed", action.id)
            action.status = "failed"
            action.result = str(exc)

    success_event = action_plugin.success_event_type(action.action_type) if action_plugin is not None else None
    event_type = (success_event or "action.executed") if action.status == "completed" else "action.failed"
    data_json: dict[str, Any] = {
        "action_id": action.id,
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target": action.target,
        "status": action.status,
        "result": action.result,
        "manual": True,
        "trigger": "manual",
    }
    # e.g. CrowdSec adds scenario/duration for ban rows so its page can show them.
    if action_plugin is not None:
        data_json.update(action_plugin.action_event_data(action))
    plugin_id = manager.plugin_id_for_action(action.action_type)
    store_event(
        db,
        source="Action Framework",
        source_id="actions",
        plugin=plugin_id,
        plugin_id=plugin_id,
        event_type=event_type,
        severity="info" if action.status == "completed" else "error",
        ip=action.target if action.target_type == "ip" else None,
        data_json=data_json,
    )
