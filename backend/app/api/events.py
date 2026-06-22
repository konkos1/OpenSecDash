from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.database.dependencies import get_db
from app.models.events import Event
from app.services.events import apply_event_filters, store_event

router = APIRouter(prefix="/api/events", tags=["events"])


class EventCreate(BaseModel):
    timestamp: datetime | None = None
    event_time: datetime | None = None
    source: str = "manual"
    source_id: str | None = None
    plugin: str = "core"
    plugin_id: str | None = None
    event_type: str
    severity: str = "info"
    ip: str | None = None
    country: str | None = None
    asn: str | None = None
    hostname: str | None = None
    asset_id: int | None = None
    method: str | None = None
    status_code: int | None = None
    path: str | None = None
    data_json: dict[str, Any] | None = None
    raw_data: str | None = None


class EventResponse(EventCreate):
    id: int
    created_at: datetime | None = None
    timestamp: datetime = Field(default_factory=utc_now)

    class Config:
        from_attributes = True


@router.post("", response_model=EventResponse)
def create_event(event_data: EventCreate, db: Session = Depends(get_db)):
    values = event_data.model_dump(exclude_none=True)
    event = store_event(db, **values)
    db.commit()
    db.refresh(event)
    return event


@router.get("", response_model=list[EventResponse])
def list_events(
    limit: int = Query(100, le=1000),
    event_type: str | None = None,
    ip: str | None = None,
    country: str | None = None,
    severity: str | None = None,
    source: str | None = None,
    plugin: str | None = None,
    status_code: int | None = None,
    path: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    query = apply_event_filters(
        db.query(Event),
        {
            "event_type": event_type,
            "ip": ip,
            "country": country.upper() if country else None,
            "severity": severity,
            "source": source,
            "plugin": plugin,
            "status_code": status_code,
            "path": path,
            "q": q,
        },
    )
    return query.order_by(Event.event_time.desc()).limit(limit).all()


@router.get("/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
