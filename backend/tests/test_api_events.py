from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api.events import EventCreate, create_event, get_event, list_events
from app.models.settings import Setting


def test_events_api_create_list_and_get(db_session):
    db_session.add(Setting(key="plugin.geoip.enabled", value="false"))
    db_session.commit()

    created = create_event(
        EventCreate(
            event_time=datetime(2026, 1, 2, 8, 0, 0),
            source="api-test",
            plugin="traefik_log",
            event_type="access.error",
            severity="warning",
            ip="8.8.8.8",
            country="US",
            status_code=404,
            path="/missing",
        ),
        db_session,
    )

    assert created.id is not None
    assert get_event(created.id, db_session).path == "/missing"
    listed = list_events(limit=10, event_type="access.*", ip="8.8.8.8", country="US", status_code=404, range="all", db=db_session)
    assert [event.id for event in listed] == [created.id]

    with pytest.raises(HTTPException) as exc_info:
        get_event(9999, db_session)
    assert exc_info.value.status_code == 404


def test_events_api_rejects_invalid_search_expression(db_session):
    with pytest.raises(HTTPException) as exc_info:
        list_events(q="term ||", range="all", db=db_session)

    assert exc_info.value.status_code == 422


def test_events_api_rejects_unbounded_custom_range(db_session):
    with pytest.raises(HTTPException) as exc_info:
        list_events(q="marker", range="custom", db=db_session)

    assert exc_info.value.status_code == 422
