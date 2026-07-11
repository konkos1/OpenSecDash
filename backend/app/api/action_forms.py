from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.database.dependencies import get_db
from app.models.core import Action
from app.plugins.manager import get_plugin_manager
from app.services.actions import ActionAlreadyRunning, create_action
from app.services.events import store_event

router = APIRouter(tags=["actions"])


@router.post("/actions/ip")
def action_ip_page(
    action_type: str = Form(...),
    ip: str = Form(...),
    duration: str = Form("4h"),
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    manager = get_plugin_manager()
    parameters: dict[str, str] = {}
    try:
        definition = next(
            (definition for _plugin_id, definition in manager.action_definitions() if definition.action_type == action_type),
            None,
        )
        if definition is None:
            raise ValueError(f"Unknown action type: {action_type}")
        for parameter in definition.parameters:
            if parameter.name != "duration":
                raise ValueError(f"Unsupported action parameter: {parameter.name}")
            value = duration or parameter.default
            if value is None:
                raise ValueError(f"Missing action parameter: {parameter.name}")
            if value not in parameter.options:
                raise ValueError(f"Invalid value for action parameter: {parameter.name}")
            parameters[parameter.name] = value
        create_action(db, action_type, ip, "ip", parameters, confirmed)
    except ActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        action = Action(
            timestamp=utc_now().replace(tzinfo=None),
            action_type=action_type,
            plugin_id=manager.plugin_id_for_action(action_type),
            target_type="ip",
            target=ip,
            parameters=parameters,
            status="failed",
            result=str(exc),
            requires_confirmation=action_type in manager.critical_action_types(),
        )
        db.add(action)
        db.flush()
        store_event(
            db,
            source="Action Framework",
            source_id="actions",
            plugin=action.plugin_id,
            plugin_id=action.plugin_id,
            event_type="action.failed",
            severity="error",
            ip=ip,
            data_json={"action_id": action.id, "action_type": action_type, "target": ip, "status": "failed", "result": str(exc), "manual": True, "trigger": "manual"},
        )
        db.commit()
    return RedirectResponse(url=f"/ip/{quote(ip, safe='')}", status_code=303)
