from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.core import Action
from app.services.actions import ActionAlreadyRunning, create_action

router = APIRouter(prefix="/api/actions", tags=["actions"])


class ActionRequest(BaseModel):
    action_type: str
    target: str
    target_type: str = "ip"
    parameters: dict | None = None
    confirmed: bool = False


@router.get("")
def list_actions(db: Session = Depends(get_db)):
    return db.query(Action).order_by(Action.timestamp.desc()).limit(100).all()


@router.get("/available")
def available_actions(target_type: str, target: str, db: Session = Depends(get_db)):
    from app.plugins.manager import get_plugin_manager

    manager = get_plugin_manager()
    return [
        {
            "action_type": definition.action_type,
            "plugin_id": plugin_id,
            "label_key": definition.label_key,
            "description_key": definition.description_key,
            "target_types": sorted(definition.target_types),
            "critical": definition.critical,
            "permission": definition.permission,
            "parameters": [
                {
                    "name": parameter.name,
                    "kind": parameter.kind,
                    "options": list(parameter.options),
                    "default": parameter.default,
                    "label_key": parameter.label_key,
                }
                for parameter in definition.parameters
            ],
        }
        for plugin_id, definition in manager.available_actions(db, target_type, target)
    ]


@router.post("")
def run_action(payload: ActionRequest, db: Session = Depends(get_db)):
    try:
        return create_action(
            db=db,
            action_type=payload.action_type,
            target=payload.target,
            target_type=payload.target_type,
            parameters=payload.parameters,
            confirmed=payload.confirmed,
        )
    except ActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
