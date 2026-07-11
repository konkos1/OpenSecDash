from types import SimpleNamespace
from typing import Any, cast

from fastapi import Request
from sqlalchemy.orm import Session

from app.web.dashboard import DashboardWidget, collect_dashboard_widgets, validate_widget


def make_widget(**kwargs) -> DashboardWidget:
    values = {
        "id": "widget",
        "type": "counter",
        "section": "security",
        "title_key": "dashboard.widget",
    }
    values.update(kwargs)
    return DashboardWidget(**cast(dict[str, Any], values))


def test_validate_widget_rejects_unknown_type_and_section():
    assert not validate_widget(make_widget(type="unknown"))
    assert not validate_widget(make_widget(section="unknown"))


def test_validate_widget_rejects_missing_identity_and_external_href():
    assert not validate_widget(make_widget(id=""))
    assert not validate_widget(make_widget(title_key=""))
    assert not validate_widget(make_widget(href="http://evil.example"))
    assert not validate_widget(make_widget(href="//evil.example"))


def test_validate_widget_accepts_internal_paths():
    assert validate_widget(make_widget(href="/events?today=true"))


def test_collect_dashboard_widgets_deduplicates_and_sorts():
    widgets = collect_dashboard_widgets(
        cast(Session, object()),
        [
            make_widget(id="feed", section="feed", order=1),
            make_widget(id="asset", section="assets", order=2),
            make_widget(id="security-b", section="security", order=20),
            make_widget(id="security-a", section="security", order=10),
            make_widget(id="security-a", section="security", order=1, title_key="dashboard.duplicate"),
            make_widget(id="invalid", type="unknown"),
        ],
    )

    assert [widget.id for widget in widgets] == ["security-a", "security-b", "asset", "feed"]
    assert widgets[0].title_key == "dashboard.widget"


def test_core_dashboard_counter_order_matches_previous_dashboard(db_session, monkeypatch):
    monkeypatch.setattr("app.api.pages.is_plugin_enabled", lambda db, plugin_id: plugin_id in {"crowdsec", "geoblock_log", "traefik_log"})

    from app.api.pages import dashboard_page

    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr("app.api.pages.render", fake_render)
    dashboard_page(cast(Request, SimpleNamespace()), db_session)

    assert [widget.id for widget in captured["dashboard_widgets"]] == [
        "crowdsec.active_bans",
        "geoblock_log.geoblocks_today",
        "traefik_log.access_external_today",
        "traefik_log.access_internal_today",
    ]
