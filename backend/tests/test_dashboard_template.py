from types import SimpleNamespace
from typing import cast

from fastapi import Request
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.api import pages
from app.models.assets import Asset
from app.models.settings import Setting
from app.models.systems import System


def render_dashboard(*, event_plugins_enabled: bool) -> str:
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
        widgets=[],
        top_countries=[],
        attack_hours=[],
        access_hours=[],
        country_heatmap=[],
        today_events_href="/events?today=true",
        country_data_plugins=["crowdsec"] if event_plugins_enabled else [],
        latest_security_events=[],
    )


def test_dashboard_does_not_show_historical_rollup_widgets():
    html = render_dashboard(event_plugins_enabled=True)

    assert "dashboard.rollups_historical" not in html
    assert "dashboard.rollup_event_types" not in html
    assert "dashboard.rollup_scenarios" not in html


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

    pages.dashboard_page(cast(Request, SimpleNamespace()), db_session)

    widgets = captured["widgets"]
    assert {widget["title_key"] for widget in widgets} == {"dashboard.assets", "dashboard.updates"}
    assert [widget["value"] for widget in widgets] == [1, 1]
