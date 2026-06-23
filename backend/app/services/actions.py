from __future__ import annotations

import asyncio
import ipaddress
import logging

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.core import Action
from app.plugins.manager import get_plugin_manager
from app.services.events import store_event

logger = logging.getLogger(__name__)

CRITICAL_ACTIONS = {"security.ban", "security.unban", "crowdsec_ban", "crowdsec_unban"}


def validate_ip_target(ip: str) -> None:
    address = ipaddress.ip_address(ip)
    if not address.is_global:
        raise ValueError("Only global IP addresses are valid action targets")


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
