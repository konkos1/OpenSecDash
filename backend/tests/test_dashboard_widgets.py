from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import Request
from sqlalchemy.orm import Session

from app.api import pages
from app.models.core import AggregationDaily, AggregationMonthly
from app.models.settings import Setting
from app.models.events import Event
from app.plugins.base import Plugin, PluginMetadata
from app.plugins.manager import PluginManager, get_plugin_manager
import app.services.dashboard_metrics as dashboard_metrics
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


def test_validate_widget_validates_table_feed_and_trend_rows():
    assert validate_widget(
        make_widget(
            type="table",
            rows=({"label": "DE", "value": 3, "href": "/events?country=DE"},),
        )
    )
    assert validate_widget(
        make_widget(
            type="feed",
            rows=({"time": datetime.now(UTC), "type": "security.ban", "ip": "8.8.8.8", "href": "/ip/8.8.8.8"},),
        )
    )
    assert validate_widget(
        make_widget(type="trend", rows=({"bucket": "2026-07-11", "value": 4},))
    )
    assert validate_widget(
        make_widget(
            id="core.country_heatmap",
            type="map",
            section="trends",
            rows=({"country": "DE", "count": 3, "x": 52.0, "y": 26.0, "radius": 6.0},),
        )
    )
    assert not validate_widget(make_widget(type="table", rows=({"label": "DE", "value": "4"},)))
    assert not validate_widget(make_widget(type="table", rows=({"label": "DE", "value": 4},)))
    assert not validate_widget(make_widget(type="feed", rows=({"type": "security.ban", "ip": "8.8.8.8", "href": "/ip/8.8.8.8"},)))
    assert not validate_widget(make_widget(type="trend", rows=({"bucket": "2026-07-11", "value": -1},)))
    assert not validate_widget(make_widget(id="plugin.map", type="map", rows=({"country": "DE", "count": 3, "x": 52.0, "y": 26.0, "radius": 6.0},)))


def test_collect_dashboard_widgets_deduplicates_and_sorts(monkeypatch):
    monkeypatch.setattr("app.web.dashboard.get_plugin_manager", lambda: SimpleNamespace(plugins={}))
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
    db_session.add_all(
        [
            Setting(key="plugin.crowdsec.enabled", value="true"),
            Setting(key="plugin.geoblock_log.enabled", value="true"),
            Setting(key="plugin.traefik_log.enabled", value="true"),
        ]
    )
    db_session.commit()

    from app.api.pages import dashboard_page

    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr("app.api.pages.render", fake_render)
    dashboard_page(cast(Request, SimpleNamespace()), db_session)

    assert [widget.id for widget in captured["dashboard_widgets"] if widget.type == "counter"] == [
        "crowdsec.active_bans",
        "geoblock_log.geoblocks_today",
        "traefik_log.access_external_today",
        "traefik_log.access_internal_today",
    ]


def test_dashboard_core_widgets_include_tables_feed_and_trend(db_session, monkeypatch):
    monkeypatch.setattr("app.api.pages.is_plugin_enabled", lambda db, plugin_id: plugin_id in {"crowdsec", "traefik_log"})
    monkeypatch.setattr("app.api.pages.utc_now", lambda: datetime(2026, 7, 11, 12, tzinfo=UTC))
    db_session.add_all(
        [
            Setting(key="plugin.crowdsec.enabled", value="true"),
            Setting(key="plugin.traefik_log.enabled", value="true"),
            AggregationDaily(date="2026-07-11", metric="summary", key="security_events", value=2),
        ]
    )
    db_session.commit()

    from app.api.pages import dashboard_page

    monkeypatch.setattr("app.api.pages.render", lambda request, db, template, **context: context)
    context = cast(dict[str, Any], dashboard_page(cast(Request, SimpleNamespace()), db_session))
    ids = {widget.id for widget in context["dashboard_widgets"]}
    assert {"core.top_countries", "core.country_heatmap", "core.top_attack_hours", "core.top_access_hours", "core.latest_security_events", "core.security_events_trend"} <= ids
    assert "core.country_heatmap" in pages.dashboard_layout_widget_ids(db_session)


def test_dashboard_page_keeps_hidden_widgets_in_editor_context(db_session, monkeypatch):
    db_session.add_all(
        [
            Setting(key="plugin.crowdsec.enabled", value="true"),
            Setting(key="ui.dashboard_layout", value='[{"id":"crowdsec.active_bans","visible":false}]'),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr("app.api.pages.render", fake_render)
    from app.api.pages import dashboard_page

    dashboard_page(cast(Request, SimpleNamespace()), db_session)

    assert "crowdsec.active_bans" not in {widget.id for widget in captured["dashboard_widgets"]}
    editor_widgets = {widget.id: widget for widget in captured["dashboard_layout_widgets"]}
    assert editor_widgets["crowdsec.active_bans"].visible is False


def test_crowdsec_dashboard_top_scenarios_uses_only_today_daily_rollup(db_session, monkeypatch):
    rollups = import_module("osd_plugins.crowdsec.services.rollups")
    monkeypatch.setattr(rollups, "utc_now", lambda: datetime(2026, 7, 11, 12, tzinfo=UTC))
    db_session.add_all(
        [
            Setting(key="plugin.crowdsec.enabled", value="true"),
            AggregationDaily(date="2026-07-11", metric="scenario", key="crowdsecurity/ssh-bf", value=4),
            AggregationDaily(date="2026-07-10", metric="scenario", key="crowdsecurity/old-daily", value=20),
            AggregationMonthly(month="2026-06", metric="scenario", key="crowdsecurity/old-monthly", value=30),
        ]
    )
    db_session.commit()

    crowdsec = get_plugin_manager().plugins["crowdsec"]
    widgets = {widget.id: widget for widget in crowdsec.dashboard_widgets(db_session)}

    scenario_widget = widgets["crowdsec.top_scenarios"]
    assert scenario_widget.type == "table"
    assert scenario_widget.title_key == "crowdsec.dashboard_top_scenarios"
    assert scenario_widget.rows[0]["label"] == "crowdsecurity/ssh-bf"
    assert scenario_widget.rows[0]["value"] == 4
    assert scenario_widget.rows[0]["href"].startswith("/events?")
    assert "security.ban" in scenario_widget.rows[0]["href"]
    assert [row["label"] for row in scenario_widget.rows] == ["crowdsecurity/ssh-bf"]


def test_collect_dashboard_widgets_includes_plugin_widgets_and_isolates_failures(monkeypatch):
    class InlinePlugin(Plugin):
        metadata = PluginMetadata(id="inline", name="Inline")

        def dashboard_widgets(self, db: Session) -> list[DashboardWidget]:
            return [make_widget(id="inline.widget", section="activity", order=1)]

    class BrokenPlugin(Plugin):
        metadata = PluginMetadata(id="broken", name="Broken")

        def dashboard_widgets(self, db: Session) -> list[DashboardWidget]:
            raise RuntimeError("broken test hook")

    manager = PluginManager(Path("unused-test-plugin-dir"))
    manager.plugins = {"inline": InlinePlugin(), "broken": BrokenPlugin()}
    monkeypatch.setattr("app.web.dashboard.get_plugin_manager", lambda: manager)

    widgets = collect_dashboard_widgets(cast(Session, object()), [])

    assert [widget.id for widget in widgets] == ["inline.widget"]


def test_plugin_counter_values_and_deltas_match_previous_dashboard(db_session, monkeypatch):
    monkeypatch.setattr(dashboard_metrics, "utc_now", lambda: datetime(2026, 7, 11, 12, 0, tzinfo=UTC))
    db_session.add_all(
        [
            Setting(key="timezone", value="UTC"),
            Setting(key="plugin.crowdsec.enabled", value="true"),
            Setting(key="plugin.geoblock_log.enabled", value="true"),
            Setting(key="plugin.traefik_log.enabled", value="true"),
        ]
    )
    db_session.add_all(
        [
            Event(event_time=datetime(2026, 7, 1, 12), event_type="security.ban", plugin="crowdsec"),
            Event(event_time=datetime(2026, 7, 10, 2), event_type="security.ban", plugin="crowdsec"),
            Event(event_time=datetime(2026, 7, 11, 2), event_type="security.ban", plugin="crowdsec"),
            Event(event_time=datetime(2026, 7, 11, 3), event_type="security.ban", plugin="crowdsec"),
            Event(event_time=datetime(2026, 7, 1, 12), event_type="security.geoblock", plugin="geoblock_log"),
            Event(event_time=datetime(2026, 7, 10, 2), event_type="security.geoblock", plugin="geoblock_log"),
            Event(event_time=datetime(2026, 7, 11, 2), event_type="security.geoblock", plugin="geoblock_log"),
            Event(event_time=datetime(2026, 7, 11, 3), event_type="security.geoblock", plugin="geoblock_log"),
            Event(event_time=datetime(2026, 7, 1, 12), event_type="access.allowed", plugin="traefik_log", ip="8.8.8.8", is_local_ip=False),
            Event(event_time=datetime(2026, 7, 10, 2), event_type="access.allowed", plugin="traefik_log", ip="8.8.8.8", is_local_ip=False),
            Event(event_time=datetime(2026, 7, 11, 2), event_type="access.allowed", plugin="traefik_log", ip="8.8.8.8", is_local_ip=False),
            Event(event_time=datetime(2026, 7, 11, 3), event_type="access.allowed", plugin="traefik_log", ip="8.8.4.4", is_local_ip=False),
            Event(event_time=datetime(2026, 7, 1, 12), event_type="access.allowed", plugin="traefik_log", ip="192.168.1.1", is_local_ip=True),
            Event(event_time=datetime(2026, 7, 10, 2), event_type="access.allowed", plugin="traefik_log", ip="192.168.1.1", is_local_ip=True),
            Event(event_time=datetime(2026, 7, 11, 2), event_type="access.allowed", plugin="traefik_log", ip="192.168.1.1", is_local_ip=True),
            Event(event_time=datetime(2026, 7, 11, 3), event_type="access.allowed", plugin="traefik_log", ip="192.168.1.2", is_local_ip=True),
        ]
    )
    db_session.commit()

    plugins = get_plugin_manager().plugins
    widgets = {
        widget.id: widget
        for plugin_id in ("crowdsec", "geoblock_log", "traefik_log")
        for widget in plugins[plugin_id].dashboard_widgets(db_session)
    }

    assert widgets["crowdsec.active_bans"].value == 2
    assert widgets["crowdsec.active_bans"].delta == {"label": "+100%", "class": "dashboard-delta-up"}
    assert widgets["geoblock_log.geoblocks_today"].value == 2
    assert widgets["geoblock_log.geoblocks_today"].delta == {"label": "+100%", "class": "dashboard-delta-up"}
    assert widgets["traefik_log.access_external_today"].value == 2
    assert widgets["traefik_log.access_external_today"].delta == {"label": "+100%", "class": "dashboard-delta-up"}
    assert widgets["traefik_log.access_internal_today"].value == 2
    assert widgets["traefik_log.access_internal_today"].delta == {"label": "+100%", "class": "dashboard-delta-up"}
