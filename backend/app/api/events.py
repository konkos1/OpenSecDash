from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.events import Event

router = APIRouter(
    prefix="/api/events",
    tags=["events"],
)


class EventCreate(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str
    plugin: str
    event_type: str
    severity: str = "info"

    ip: str | None = None
    country: str | None = None
    hostname: str | None = None
    status_code: int | None = None
    path: str | None = None

    data_json: dict[str, Any] | None = None


class EventResponse(EventCreate):
    id: int

    class Config:
        from_attributes = True


@router.post("", response_model=EventResponse)
def create_event(
    event_data: EventCreate,
    db: Session = Depends(get_db),
):
    event = Event(**event_data.model_dump())

    db.add(event)
    db.commit()
    db.refresh(event)

    return event


@router.get("", response_model=list[EventResponse])
def list_events(
    limit: int = 100,
    event_type: str | None = None,
    ip: str | None = None,
    country: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    plugin: str | None = None,
    status_code: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Event)

    if event_type:
        if event_type.endswith("*"):
            query = query.filter(
                Event.event_type.startswith(event_type[:-1])
            )
        else:
            query = query.filter(Event.event_type == event_type)

    if ip:
        query = query.filter(Event.ip == ip)

    if country:
        query = query.filter(Event.country == country.upper())

    if severity:
        query = query.filter(Event.severity == severity)

    if source:
        query = query.filter(Event.source == source)

    if plugin:
        query = query.filter(Event.plugin == plugin)

    if status_code:
        query = query.filter(Event.status_code == status_code)

    return (
        query
        .order_by(Event.timestamp.desc())
        .limit(limit)
        .all()
    )


@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: int,
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found",
        )

    return event
