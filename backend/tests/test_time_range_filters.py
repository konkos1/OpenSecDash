from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.api import pages
from app.models.settings import Setting
from app.web import tables
from app.web.tables import time_range_start
from conftest import import_plugin_module


def _request(query_params: dict[str, str] | None = None):
    return SimpleNamespace(query_params=query_params or {}, url=SimpleNamespace(path="/events"), headers={"HX-Request": "true"})


@pytest.mark.parametrize(
    ("range_value", "offset"),
    [("1h", timedelta(hours=1)), ("24h", timedelta(hours=24)), ("7d", timedelta(days=7)), ("30d", timedelta(days=30))],
)
def test_time_range_presets_calculate_utc_start(monkeypatch, range_value, offset):
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(tables, "utc_now", lambda: now)

    assert time_range_start(range_value) == (now - offset).replace(tzinfo=None)


def test_events_page_uses_explicit_range_and_saves_it(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(tables, "utc_now", lambda: now)
    monkeypatch.setattr(pages, "apply_event_filters", lambda query, filters: captured.update(filters) or query)
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: context)

    pages.events_page(cast(Any, _request()), range="24h", db=db_session)

    assert captured["event_time_from"] == datetime(2026, 7, 11, 12, 0)
    assert db_session.query(Setting).filter(Setting.key == "ui.time_range").one().value == "24h"


def test_event_pages_use_saved_range_only_without_url_value(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.add(Setting(key="ui.time_range", value="7d"))
    db_session.commit()
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(tables, "utc_now", lambda: now)
    events_filters: dict[str, Any] = {}
    monkeypatch.setattr(pages, "apply_event_filters", lambda query, filters: events_filters.update(filters) or query)
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: context)

    pages.events_page(cast(Any, _request()), db=db_session)

    routes = import_plugin_module("traefik_log", "routes")
    access_filters: dict[str, Any] = {}
    monkeypatch.setattr(routes, "apply_event_filters", lambda query, filters: access_filters.update(filters) or query)
    monkeypatch.setattr(routes, "render", lambda request, db, template, **context: context)
    routes.access_page(cast(Any, _request()), range="1h", db=db_session)

    assert events_filters["event_time_from"] == datetime(2026, 7, 5, 12, 0)
    assert access_filters["event_time_from"] == datetime(2026, 7, 12, 11, 0)
    assert db_session.query(Setting).filter(Setting.key == "ui.time_range").one().value == "1h"


def test_custom_range_uses_url_boundaries(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(pages, "apply_event_filters", lambda query, filters: captured.update(filters) or query)
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: context)

    pages.events_page(
        cast(Any, _request()),
        range="custom",
        from_="2026-07-01T00:00:00Z",
        to="2026-07-02T00:00:00Z",
        db=db_session,
    )

    assert captured["event_time_from"] == datetime(2026, 7, 1, 0, 0)
    assert captured["event_time_to"] == datetime(2026, 7, 2, 0, 0)


def test_event_pages_default_missing_or_empty_range_to_24_hours(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(tables, "utc_now", lambda: now)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(pages, "apply_event_filters", lambda query, filters: captured.update(filters) or query)
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: context)

    pages.events_page(cast(Any, _request()), db=db_session)
    assert captured["event_time_from"] == datetime(2026, 7, 11, 12, 0)

    pages.events_page(cast(Any, _request()), range="", db=db_session)
    assert captured["event_time_from"] == datetime(2026, 7, 11, 12, 0)


def test_explicit_all_time_range_is_saved_and_unbounded(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(pages, "apply_event_filters", lambda query, filters: captured.update(filters) or query)
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: context)

    context = cast(dict[str, Any], pages.events_page(cast(Any, _request()), range="all", db=db_session))

    assert captured["event_time_from"] is None
    assert context["filters"]["range"] == "all"
    assert db_session.query(Setting).filter(Setting.key == "ui.time_range").one().value == "all"
