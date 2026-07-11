import json
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import pages
from app.core.template_context import get_setting_value
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.web.dashboard import DashboardWidget, WidgetSection, apply_layout, load_dashboard_layout


@pytest.fixture()
def layout_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dashboard-layout.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def make_layout_widget(widget_id: str, section: WidgetSection, order: int) -> DashboardWidget:
    return DashboardWidget(
        id=widget_id,
        type="counter",
        section=section,
        title_key=f"dashboard.{widget_id}",
        order=order,
        href="/",
    )


def encoded_form(fields: list[tuple[str, str]]) -> bytes:
    return urlencode(fields).encode("ascii")


def test_apply_layout_orders_visibility_and_handles_stale_and_new_widgets():
    widgets = [
        make_layout_widget("natural-late", "security", 20),
        make_layout_widget("natural-first", "security", 10),
        make_layout_widget("new-widget", "activity", 10),
    ]

    applied = apply_layout(
        widgets,
        [
            {"id": "natural-late", "visible": False},
            {"id": "removed-widget", "visible": True},
        ],
    )

    assert [widget.id for widget in applied] == ["natural-late", "natural-first", "new-widget"]
    assert [widget.visible for widget in applied] == [False, True, True]


def test_apply_layout_stored_order_overrides_default_type_order():
    widgets = [
        make_layout_widget("counter", "security", 10),
        DashboardWidget(
            id="table",
            type="table",
            section="trends",
            title_key="dashboard.table",
            order=10,
            rows=({"label": "DE", "value": 1, "href": "/events"},),
        ),
        DashboardWidget(
            id="feed",
            type="feed",
            section="feed",
            title_key="dashboard.feed",
            order=10,
            rows=({"time": "2026-07-11", "type": "security.ban", "ip": "8.8.8.8", "href": "/events"},),
        ),
    ]

    applied = apply_layout(
        widgets,
        [
            {"id": "feed", "visible": True},
            {"id": "counter", "visible": True},
            {"id": "table", "visible": True},
        ],
    )

    assert [widget.id for widget in applied] == ["feed", "counter", "table"]


def test_apply_layout_missing_or_empty_layout_uses_natural_order(db_session):
    widgets = [
        make_layout_widget("late", "security", 20),
        make_layout_widget("first", "security", 10),
        DashboardWidget(
            id="table",
            type="table",
            section="trends",
            title_key="dashboard.table",
            order=1,
            rows=({"label": "DE", "value": 1, "href": "/events"},),
        ),
    ]

    assert [widget.id for widget in apply_layout(widgets, [])] == ["first", "late", "table"]
    assert load_dashboard_layout(db_session) == []


def test_dashboard_layout_route_enforces_allowlist_and_reset(layout_db, monkeypatch):
    monkeypatch.setattr(pages, "dashboard_layout_widget_ids", lambda db: {"first", "second"})
    app.dependency_overrides[get_db] = lambda: layout_db
    client = TestClient(app)
    try:
        response = client.post(
            "/dashboard/layout",
            content=encoded_form([
                ("widget_id", "second"),
                ("widget_id", "first"),
                ("widget_id", "removed-widget"),
                ("visible", "second"),
            ]),
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        stored = json.loads(get_setting_value(layout_db, "ui.dashboard_layout", ""))
        assert stored == [
            {"id": "second", "visible": True},
            {"id": "first", "visible": False},
        ]

        response = client.post(
            "/dashboard/layout",
            content=encoded_form([
                ("widget_id", "second"),
                ("widget_id", "first"),
                ("visible", "second"),
                ("move_down", "second"),
            ]),
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        moved = json.loads(get_setting_value(layout_db, "ui.dashboard_layout", ""))
        assert moved == [
            {"id": "first", "visible": False},
            {"id": "second", "visible": True},
        ]

        response = client.post("/dashboard/layout/reset", follow_redirects=False)
        assert response.status_code == 303
        assert layout_db.query(pages.Setting).filter_by(key="ui.dashboard_layout").count() == 0
    finally:
        client.close()
        app.dependency_overrides.clear()
