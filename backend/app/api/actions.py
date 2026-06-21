from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.core import Action
from app.services.actions import create_action

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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
