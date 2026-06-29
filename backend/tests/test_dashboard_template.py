from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_dashboard(*, rollup_plugins_enabled: bool) -> str:
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
        enabled_plugins={"crowdsec": rollup_plugins_enabled, "geoblock_log": False, "traefik_log": False, "apps_inventory": False},
        event_plugins_enabled=rollup_plugins_enabled,
        t=lambda key: key,
        widgets=[],
        top_countries=[],
        attack_hours=[],
        access_hours=[],
        country_heatmap=[],
        rollup_plugins_enabled=rollup_plugins_enabled,
        rollup_day="2026-06-29",
        rollup_event_types=[],
        rollup_scenarios=[],
        rollup_total=0,
        today_events_href="/events?today=true",
        country_data_plugins=["crowdsec"] if rollup_plugins_enabled else [],
        latest_security_events=[],
    )


def test_dashboard_shows_rollup_widget_without_rollup_data_when_data_plugin_enabled():
    html = render_dashboard(rollup_plugins_enabled=True)

    assert "dashboard.rollups_historical" in html
    assert "dashboard.rollup_event_types" in html
    assert "dashboard.rollup_scenarios" in html
    assert "dashboard.no_data" in html


def test_dashboard_hides_rollup_widget_when_no_rollup_data_plugin_enabled():
    html = render_dashboard(rollup_plugins_enabled=False)

    assert "dashboard.rollups_historical" not in html
    assert "dashboard.rollup_event_types" not in html
    assert "dashboard.rollup_scenarios" not in html
