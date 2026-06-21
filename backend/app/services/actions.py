from __future__ import annotations

import ipaddress
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.models.core import Action
from app.services.events import store_event

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
        timestamp=datetime.utcnow(),
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
    execute_action(db, action)
    db.commit()
    return action


def execute_action(db: Session, action: Action) -> None:
    dry_run = get_setting_value(db, "action_dry_run", "true").lower() == "true"
    action.status = "running"

    if dry_run:
        action.status = "completed"
        action.result = "dry-run: action was recorded but not executed"
    else:
        # V1 keeps browser -> API -> action framework. Real CrowdSec execution is
        # isolated here and can be replaced by a plugin implementation.
        action.status = "completed"
        action.result = "action plugin execution placeholder completed"

    event_type = "action.executed" if action.status == "completed" else "action.failed"
    store_event(
        db,
        source="Action Framework",
        source_id="actions",
        plugin="core",
        plugin_id="core",
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
        },
    )
