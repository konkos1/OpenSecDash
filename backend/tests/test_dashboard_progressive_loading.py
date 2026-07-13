from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import pages
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.events import Event

# A distinctive event whose data only the heavy data path renders (shell mode
# must not leak it): its IP shows up in the latest-security-events feed and its
# country in the top-countries table.
MARKER_IP = "203.0.113.77"
MARKER_COUNTRY = "ZZ"

PLUGIN_STATE = (
    ["crowdsec"],
    {"json_assets": False, "proxmox_assets": False, "crowdsec": True, "geoblock_log": False, "traefik_log": False},
    ["crowdsec"],
)
DISABLED_PLUGIN_STATE = (
    [],
    {"json_assets": False, "proxmox_assets": False, "crowdsec": False, "geoblock_log": False, "traefik_log": False},
    [],
)


@pytest.fixture()
def dashboard_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'dashboard-progressive.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    db.add(
        Event(
            event_time=datetime(2026, 7, 13, 12),
            event_type="security.ban",
            plugin="crowdsec",
            ip=MARKER_IP,
            country=MARKER_COUNTRY,
        )
    )
    db.commit()

    # Deterministic widget set: today's rollup key is absent so top_countries
    # falls back to the plain event query, and today_start is far in the past so
    # the seeded event always counts.
    monkeypatch.setattr(pages, "today_start", lambda db: datetime(1970, 1, 1))
    monkeypatch.setattr(pages, "dashboard_today_rollup_key", lambda since: None)

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    try:
        yield client, monkeypatch
    finally:
        client.close()
        app.dependency_overrides.clear()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_shell_navigation_defers_heavy_widgets(dashboard_client):
    client, monkeypatch = dashboard_client
    monkeypatch.setattr(pages, "dashboard_widget_plugin_state", lambda db: PLUGIN_STATE)

    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    # Shell shows skeletons that load the real widgets in, with a single load trigger.
    assert 'aria-busy="true"' in body
    assert 'hx-trigger="load"' in body
    assert "skeleton" in body
    # ...but none of the heavy widget data is rendered on first paint.
    assert MARKER_IP not in body
    assert f"country={MARKER_COUNTRY}" not in body


def test_shell_navigation_does_not_run_heavy_queries(dashboard_client):
    client, monkeypatch = dashboard_client
    monkeypatch.setattr(pages, "dashboard_widget_plugin_state", lambda db: PLUGIN_STATE)
    calls: list[str] = []
    real_trend = pages.dashboard_trend_rows
    monkeypatch.setattr(pages, "dashboard_trend_rows", lambda db, end_date: calls.append("trend") or real_trend(db, end_date))

    client.get("/")

    assert calls == []


def test_data_request_renders_widgets_without_load_trigger(dashboard_client):
    client, monkeypatch = dashboard_client
    monkeypatch.setattr(pages, "dashboard_widget_plugin_state", lambda db: PLUGIN_STATE)
    calls: list[str] = []
    real_trend = pages.dashboard_trend_rows
    monkeypatch.setattr(pages, "dashboard_trend_rows", lambda db, end_date: calls.append("trend") or real_trend(db, end_date))

    response = client.get("/", headers={"HX-Request": "true"})

    assert response.status_code == 200
    body = response.text
    # The data path runs the heavy queries and renders the real widget data.
    assert calls == ["trend"]
    assert MARKER_IP in body
    assert f"country={MARKER_COUNTRY}" in body
    # No load trigger on the data response, so it neither loops nor re-defers.
    assert 'hx-trigger="load"' not in body


def test_data_request_respects_plugin_feature_flags(dashboard_client):
    client, monkeypatch = dashboard_client
    monkeypatch.setattr(pages, "dashboard_widget_plugin_state", lambda db: DISABLED_PLUGIN_STATE)

    response = client.get("/", headers={"HX-Request": "true"})

    assert response.status_code == 200
    # Same gating as the full page: with the security plugin disabled the data
    # path must not surface its events, even though it is the "real data" path.
    assert MARKER_IP not in response.text
