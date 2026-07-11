from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import cast

from fastapi import Request

from app.api import pages
from app.models.core import Insight


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

    pages.dashboard_page(cast(Request, SimpleNamespace()), db_session)

    top_insights = captured["top_insights"]

    assert len(top_insights) == 5
    assert top_insights[0] == {"type": "today.top", "count": 2, "title": "Top"}
    assert "old.insight" not in {insight["type"] for insight in top_insights}
