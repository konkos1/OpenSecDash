from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

from fastapi import Request
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.api import pages
from app.models.assets import Asset
from app.models.settings import Setting
from app.models.systems import System
from app.web.dashboard import DashboardWidget


def render_dashboard(*, event_plugins_enabled: bool, dashboard_widgets: list[DashboardWidget] | None = None) -> str:
    all_dashboard_widgets = dashboard_widgets or []
    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["url_path_quote"] = lambda value: str(value)
    env.filters["datetime"] = lambda value: str(value)
    env.filters["country_or_local"] = lambda value, ip=None: str(value or "")
    env.filters["country_name"] = lambda value: str(value or "")
    template = env.get_template("dashboard.html")

    return template.render(
        request=SimpleNamespace(url=SimpleNamespace(path="/")),
        language="en",
        theme="dark",
        timezone="auto",
        domain="homelab.example",
        enabled_plugins={"crowdsec": event_plugins_enabled, "geoblock_log": False, "traefik_log": False, "json_assets": False, "proxmox_assets": False},
        event_plugins_enabled=event_plugins_enabled,
        t=lambda key: key,
        dashboard_widgets=[widget for widget in all_dashboard_widgets if widget.visible],
        dashboard_layout_widgets=all_dashboard_widgets,
        event_data_plugins_enabled=event_plugins_enabled,
        top_countries=[],
        attack_hours=[],
        access_hours=[],
        today_events_href="/events?today=true",
        country_data_plugins=["crowdsec"] if event_plugins_enabled else [],
        latest_security_events=[],
        dashboard_local_date="2026-07-29",
    )


def test_dashboard_title_separates_local_date_with_middle_dot():
    html = render_dashboard(event_plugins_enabled=True)

    assert "dashboard.ui_title · 2026-07-29" in html
    assert "dashboard.ui_title (2026-07-29)" not in html


def test_dashboard_does_not_show_historical_rollup_widgets():
    html = render_dashboard(event_plugins_enabled=True)

    assert "dashboard.rollups_historical" not in html
    assert "dashboard.rollup_event_types" not in html
    assert "dashboard.rollup_scenarios" not in html


def test_dashboard_renders_counter_descriptors_in_order():
    html = render_dashboard(
        event_plugins_enabled=True,
        dashboard_widgets=[
            DashboardWidget(
                id="crowdsec.active_bans",
                type="counter",
                section="security",
                title_key="dashboard.active_bans",
                value=7,
                href="/events?event_type=security.ban*&today=true",
            ),
            DashboardWidget(
                id="geoblock_log.geoblocks_today",
                type="counter",
                section="security",
                title_key="dashboard.geoblocks_today",
                value=11,
                href="/events?event_type=security.geoblock&today=true",
            ),
        ],
    )

    assert html.index(">7<") < html.index(">11<")


def test_dashboard_renders_mixed_widget_types_in_layout_order():
    html = render_dashboard(
        event_plugins_enabled=True,
        dashboard_widgets=[
            DashboardWidget(
                id="core.feed_first",
                type="feed",
                section="feed",
                title_key="dashboard.feed_first",
                rows=({"time": datetime(2026, 7, 11, 12), "type": "security.ban", "ip": "8.8.8.8", "href": "/events"},),
            ),
            DashboardWidget(
                id="core.counter_second",
                type="counter",
                section="security",
                title_key="dashboard.counter_second",
                value=2,
                href="/events",
            ),
            DashboardWidget(
                id="core.table_third",
                type="table",
                section="trends",
                title_key="dashboard.table_third",
                rows=({"label": "DE", "value": 3, "href": "/events"},),
            ),
        ],
    )

    rendered = html.split('<div id="dashboard-results"', 1)[1]
    assert rendered.index("dashboard.feed_first") < rendered.index("dashboard.counter_second") < rendered.index("dashboard.table_third")


def test_dashboard_layout_reordering_waits_for_apply():
    html = render_dashboard(
        event_plugins_enabled=True,
        dashboard_widgets=[
            DashboardWidget(
                id="crowdsec.active_bans",
                type="counter",
                section="security",
                title_key="dashboard.active_bans",
                value=7,
                href="/events?event_type=security.ban*&today=true",
            )
        ],
    )

    assert 'data-dashboard-move="up"' in html
    assert 'data-dashboard-move="down"' in html
    assert 'name="move_up"' not in html
    assert 'name="move_down"' not in html
    assert 'type="submit">dashboard.apply_layout</button>' in html


def test_dashboard_renders_table_feed_trend_and_empty_states():
    html = render_dashboard(
        event_plugins_enabled=True,
        dashboard_widgets=[
            DashboardWidget(
                id="core.table",
                type="table",
                section="trends",
                title_key="dashboard.top_countries",
                rows=({"label": "DE", "value": 3, "href": "/events?country=DE"},),
            ),
            DashboardWidget(
                id="core.empty_table",
                type="table",
                section="trends",
                title_key="dashboard.top_countries",
                empty_key="dashboard.no_data",
            ),
            DashboardWidget(
                id="core.country_heatmap",
                type="map",
                section="trends",
                title_key="dashboard.country_heatmap",
                rows=({"country": "DE", "count": 3, "x": 52.0, "y": 26.0, "radius": 6.0},),
            ),
            DashboardWidget(
                id="core.feed",
                type="feed",
                section="feed",
                title_key="dashboard.latest_security_events",
                rows=({"time": datetime(2026, 7, 11, 12), "type": "security.ban", "ip": "8.8.8.8", "href": "/events?ip=8.8.8.8"},),
            ),
            DashboardWidget(
                id="core.trend",
                type="trend",
                section="trends",
                title_key="dashboard.security_events_trend",
                rows=({"bucket": "2026-07-11", "value": 2},),
            ),
        ],
    )

    assert "dashboard.top_countries" in html
    assert "dashboard.no_data" in html
    assert "world-map" in html
    assert "security.ban" in html
    assert "2026-07-11" in html
    assert "dashboard-trend-axis" in html
    assert "07-11" in html
    assert "min-height: 3px" in html
    assert "bg-sky-400" in html
    assert "dashboard.security_events_trend_help" in html


def test_dashboard_distinguishes_disabled_data_plugins_from_hidden_widgets():
    disabled_html = render_dashboard(event_plugins_enabled=False)
    hidden_html = render_dashboard(
        event_plugins_enabled=True,
        dashboard_widgets=[
            DashboardWidget(
                id="crowdsec.active_bans",
                type="counter",
                section="security",
                title_key="dashboard.active_bans",
                value=1,
                href="/events?event_type=security.ban*&today=true",
                visible=False,
            )
        ],
    )

    assert "dashboard.no_enabled_widgets" in disabled_html
    assert "dashboard.no_visible_widgets" not in disabled_html
    assert "dashboard.no_visible_widgets" in hidden_html
    assert "dashboard.no_enabled_widgets" not in hidden_html


def test_dashboard_local_date_uses_configured_timezone(db_session, monkeypatch):
    db_session.add(Setting(key="timezone", value="Pacific/Kiritimati"))
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)
    monkeypatch.setattr(pages, "utc_now", lambda: datetime(2026, 7, 28, 23, 30, tzinfo=timezone.utc))

    pages.dashboard_page(cast(Request, SimpleNamespace(headers={"HX-Request": "true"})), db_session)

    assert captured["dashboard_local_date"] == "2026-07-29"


def test_dashboard_treats_missing_today_rollup_metrics_as_zero(db_session, monkeypatch):
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)
    monkeypatch.setattr(pages, "rollup_summary", lambda db, period, value: {"bans": 1, "security_events": 1, "total_events": 1})
    monkeypatch.setattr(pages, "dashboard_today_rollup_key", lambda since: "2026-07-11")

    pages.dashboard_page(cast(Request, SimpleNamespace(headers={"HX-Request": "true"})), db_session)

    assert captured["dashboard_widgets"] == []


def test_dashboard_asset_widgets_show_for_proxmox_assets(db_session, monkeypatch):
    db_session.add(Setting(key="plugin.proxmox_assets.enabled", value="true"))
    system = System(vmid="100", hostname="vm-100", system_type="vm", source_plugin="proxmox_assets", external_id="node/qemu/100")
    db_session.add(system)
    db_session.flush()
    db_session.add(
        Asset(
            system_id=system.id,
            name="app",
            hostname="vm-100",
            source_plugin="proxmox_assets",
            external_id="node/qemu/100/app/app",
            is_active=True,
            update_available=True,
        )
    )
    db_session.commit()

    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.dashboard_page(cast(Request, SimpleNamespace(headers={"HX-Request": "true"})), db_session)

    widgets = captured["dashboard_widgets"]
    assert {widget.title_key for widget in widgets} == {"dashboard.assets", "dashboard.updates"}
    assert [widget.value for widget in widgets] == [1, 1]
