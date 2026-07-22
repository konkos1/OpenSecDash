from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.events import Event
from app.services.events import MAX_SEARCH_LENGTH, apply_event_filters, store_event, validate_search_expression
from app.web.tables import DEFAULT_EVENT_TIME_RANGE, parse_snapshot_before, time_range_start

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
    city: str | None = None
    asn: str | None = None
    isp: str | None = None
    hostname: str | None = None
    asset_id: int | None = None
    method: str | None = None
    status_code: int | None = None
    path: str | None = None
    data_json: dict[str, Any] | None = None
    raw_data: str | None = None


class EventResponse(EventCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime | None = None
    timestamp: datetime | None = None


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
    q: Annotated[str | None, Query(max_length=MAX_SEARCH_LENGTH)] = None,
    include_raw_data: bool = False,
    range: Annotated[str, Query(pattern=r"^(all|1h|24h|7d|30d|custom)$")] = DEFAULT_EVENT_TIME_RANGE,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        validate_search_expression(q or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    event_time_from = time_range_start(range, from_)
    if range == "custom" and event_time_from is None:
        raise HTTPException(status_code=422, detail="Custom time range requires a valid from value.")
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
            "include_raw_data": include_raw_data,
            "event_time_from": event_time_from,
            "event_time_to": parse_snapshot_before(to) if range == "custom" else None,
        },
    )
    return query.order_by(Event.event_time.desc()).limit(limit).all()


@router.get("/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event
