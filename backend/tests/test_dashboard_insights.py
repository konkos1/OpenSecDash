from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import cast

from fastapi import Request

from app.api import pages
from app.models.core import Insight
from app.models.settings import Setting


def test_dashboard_top_insights_groups_limits_and_filters_to_today(db_session, monkeypatch):
    start = datetime(2026, 7, 12, 0, 0)
    db_session.add_all(
        [
            Insight(timestamp=start + timedelta(hours=1), type="today.top", title="Top", description="", level="high"),
            Insight(timestamp=start + timedelta(hours=2), type="today.top", title="Top", description="", level="high"),
            Insight(timestamp=start + timedelta(hours=3), type="today.second", title="Second", description="", level="medium"),
            Insight(timestamp=start + timedelta(hours=4), type="today.third", title="Third", description="", level="low"),
            Insight(timestamp=start + timedelta(hours=5), type="today.fourth", title="Fourth", description="", level="low"),
            Insight(timestamp=start + timedelta(hours=6), type="today.fifth", title="Fifth", description="", level="low"),
            Insight(timestamp=start + timedelta(hours=7), type="today.sixth", title="Sixth", description="", level="low"),
            Insight(timestamp=start - timedelta(seconds=1), type="old.insight", title="Old", description="", level="low"),
        ]
    )
    db_session.commit()
    captured = {}

    monkeypatch.setattr(pages, "today_start", lambda db: start)
    monkeypatch.setattr(
        pages,
        "dashboard_widget_plugin_state",
        lambda db: (["traefik_log"], {"json_assets": False, "proxmox_assets": False, "crowdsec": False, "geoblock_log": False, "traefik_log": True}, []),
    )
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: captured.update(context) or context)

    pages.dashboard_page(cast(Request, SimpleNamespace(headers={"HX-Request": "true"})), db_session)

    widgets = {widget.id: widget for widget in captured["dashboard_widgets"]}
    top_insights = widgets["core.top_insights"].rows

    assert len(top_insights) == 5
    assert top_insights[0] == {"label": "Top", "insight_type": "today.top", "value": 2, "href": "/events?today=true"}
    assert "old.insight" not in {insight["insight_type"] for insight in top_insights}
    assert "core.top_insights" in {widget.id for widget in captured["dashboard_layout_widgets"]}


def test_dashboard_top_insights_widget_respects_saved_layout_visibility(db_session, monkeypatch):
    db_session.add(Setting(key="ui.dashboard_layout", value='[{"id":"core.top_insights","visible":false}]'))
    db_session.commit()
    captured = {}

    monkeypatch.setattr(
        pages,
        "dashboard_widget_plugin_state",
        lambda db: (["traefik_log"], {"json_assets": False, "proxmox_assets": False, "crowdsec": False, "geoblock_log": False, "traefik_log": True}, []),
    )
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: captured.update(context) or context)

    pages.dashboard_page(cast(Request, SimpleNamespace(headers={"HX-Request": "true"})), db_session)

    assert "core.top_insights" not in {widget.id for widget in captured["dashboard_widgets"]}
    assert {widget.id: widget for widget in captured["dashboard_layout_widgets"]}["core.top_insights"].visible is False
