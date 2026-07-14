"""Phase 2 progressive loading: shell vs data path for the heavy pages.

Each route is called directly with a real Request (with or without the
HX-Request header) and rendered for real, so the assertions cover both the
route split (discriminator, guards) and the template (skeleton + one
hx-trigger="load" in the shell, real data and no load trigger in the data path).
See docs/internal/progressive-widget-loading/.
"""
from datetime import datetime
from typing import Any

import pytest
from starlette.requests import Request

# Importing the app registers the plugin template dirs on the shared templates
# singleton, so plugin pages (/access, /crowdsec) render for real here too.
import app.main  # noqa: F401
from app.api import pages
from app.models.assets import Asset
from app.models.core import AggregationMonthly
from app.models.events import Event
from app.models.settings import Setting
from app.models.systems import System
from conftest import import_plugin_module


def _req(path: str, *, hx: bool, query_string: bytes = b"") -> Request:
    headers = [(b"hx-request", b"true")] if hx else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": headers,
            "query_string": query_string,
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _html(response: Any) -> str:
    return bytes(response.body).decode()


def _enable(db, *plugin_ids: str) -> None:
    for plugin_id in plugin_ids:
        db.add(Setting(key=f"plugin.{plugin_id}.enabled", value="true"))
    db.commit()


def _assert_shell(html: str, *, marker: str) -> None:
    assert 'hx-trigger="load"' in html
    assert html.count('hx-trigger="load"') == 1
    assert 'aria-busy="true"' in html
    assert "skeleton" in html
    assert marker not in html


def _assert_data(html: str, *, marker: str) -> None:
    assert 'hx-trigger="load"' not in html
    assert marker in html


def test_events_renders_data_on_initial_and_htmx_requests(db_session):
    _enable(db_session, "crowdsec")
    db_session.add(Event(event_time=datetime(2026, 7, 13, 12), event_type="security.ban", plugin="crowdsec", ip="203.0.113.77", country="ZZ", hostname="h"))
    db_session.commit()

    shell = _html(pages.events_page(_req("/events", hx=False), db=db_session))
    data = _html(pages.events_page(_req("/events", hx=True), db=db_session))

    _assert_data(shell, marker="203.0.113.77")
    _assert_data(data, marker="203.0.113.77")


def test_events_initial_request_has_no_deferred_fetch(db_session):
    _enable(db_session, "crowdsec")
    shell = _html(pages.events_page(_req("/events", hx=False, query_string=b"country=ZZ&ip=203.0.113.77"), db=db_session))
    assert 'hx-get="http://testserver/events' not in shell
    assert 'id="events-results"' in shell


def test_events_mobile_rows_match_access_layout_and_open_long_user_agent(db_session):
    _enable(db_session, "crowdsec")
    db_session.add(Setting(key="ui.events.visible_columns", value="time,type,path,url,user_agent"))
    db_session.add(
        Event(
            event_time=datetime(2026, 7, 13, 12),
            event_type="security.mobilemarker",
            plugin="crowdsec",
            path="/a/very/long/path",
            data_json={"user_agent": "Long Mobile User Agent / 1.0"},
        )
    )
    db_session.add(
        Event(
            event_time=datetime(2026, 7, 13, 11),
            event_type="security.mobileempty",
            plugin="crowdsec",
        )
    )
    db_session.commit()

    html = _html(pages.events_page(_req("/events", hx=False), db=db_session))

    assert 'class="card overflow-x-auto responsive-table"' in html
    assert 'data-label="Time"' in html
    assert 'data-label="Type"' in html
    assert 'class="path-cell cell-stack" data-label="Path"' in html
    assert 'class="path-cell cell-stack" data-label="URL"' in html
    assert 'class="path-cell cell-stack" data-label="User-Agent"' in html
    assert 'class="path-truncate text-action" data-full-text="Long Mobile User Agent / 1.0"' in html
    assert 'class="path-cell" data-label="Path">-</td>' in html
    assert 'class="path-cell" data-label="URL">-</td>' in html
    assert 'class="path-cell" data-label="User-Agent">-</td>' in html


def test_events_guard_applies_in_shell(db_session):
    # No datasource plugin enabled: the guard must fire on the shell request,
    # not only on the data path.
    with pytest.raises(Exception) as exc_info:
        pages.events_page(_req("/events", hx=False), db=db_session)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_rollups_shell_defers_and_data_loads(db_session):
    _enable(db_session, "crowdsec")
    db_session.add(AggregationMonthly(month="2026-07", metric="event_type", key="security.rollupmarker", value=13))
    db_session.commit()

    shell = _html(pages.rollups_page(_req("/rollups", hx=False), db=db_session))
    data = _html(pages.rollups_page(_req("/rollups", hx=True), db=db_session))

    _assert_shell(shell, marker="security.rollupmarker")
    _assert_data(data, marker="security.rollupmarker")


def test_ip_explorer_shell_defers_and_data_loads(db_session):
    _enable(db_session, "crowdsec")
    db_session.add(Event(event_time=datetime(2026, 7, 13, 12), event_type="security.ipmarker", plugin="crowdsec", ip="203.0.113.77"))
    db_session.commit()

    shell = _html(pages.ip_explorer_page("203.0.113.77", _req("/ip/203.0.113.77", hx=False), db=db_session))
    data = _html(pages.ip_explorer_page("203.0.113.77", _req("/ip/203.0.113.77", hx=True), db=db_session))

    _assert_shell(shell, marker="security.ipmarker")
    _assert_data(data, marker="security.ipmarker")


def test_ip_explorer_encoded_path_passed_through(db_session):
    _enable(db_session, "crowdsec")
    # A CIDR network target keeps its encoded path in the deferred fetch URL.
    shell = _html(pages.ip_explorer_page("192.0.2.0%2F24", _req("/ip/192.0.2.0%2F24", hx=False), db=db_session))
    assert 'hx-get="http://testserver/ip/192.0.2.0%2F24"' in shell


def test_ip_explorer_large_network_keeps_result_materialization_bounded(db_session):
    _enable(db_session, "crowdsec")
    db_session.add_all(
        Event(event_time=datetime(2026, 7, 13, 12, index % 60), event_type="security.ipmarker", plugin="crowdsec", ip=f"203.0.113.{index % 255}")
        for index in range(300)
    )
    db_session.commit()

    events = pages._events_for_ip_target(db_session, "0.0.0.0/0", 200)

    assert len(events) == 200


def test_asset_page_shell_defers_and_data_loads(db_session):
    _enable(db_session, "json_assets")
    system = System(vmid="100", hostname="sys1", system_type="vm", source_plugin="json_assets", external_id="sys1")
    db_session.add(system)
    db_session.flush()
    asset = Asset(system_id=system.id, name="app1", source_plugin="json_assets", external_id="app1", is_active=True)
    db_session.add(asset)
    db_session.flush()
    db_session.add(Event(event_time=datetime(2026, 7, 13, 12), event_type="access.assetmarker", plugin="traefik_log", asset_id=asset.id))
    db_session.commit()

    shell = _html(pages.asset_page(system.id, _req(f"/assets/system/{system.id}", hx=False), db=db_session))
    data = _html(pages.asset_page(system.id, _req(f"/assets/system/{system.id}", hx=True), db=db_session))

    _assert_shell(shell, marker="access.assetmarker")
    _assert_data(data, marker="access.assetmarker")


def test_asset_page_unknown_system_404_in_shell(db_session):
    _enable(db_session, "json_assets")
    with pytest.raises(Exception) as exc_info:
        pages.asset_page(999999, _req("/assets/system/999999", hx=False), db=db_session)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_access_renders_data_on_initial_and_htmx_requests(db_session):
    _enable(db_session, "traefik_log")
    routes = import_plugin_module("traefik_log", "routes")
    db_session.add(Event(event_time=datetime(2026, 7, 13, 12), event_type="access.log", plugin="traefik_log", ip="198.51.100.5", is_local_ip=False))
    db_session.commit()

    shell = _html(routes.access_page(_req("/access", hx=False), db=db_session))
    data = _html(routes.access_page(_req("/access", hx=True), db=db_session))

    _assert_data(shell, marker="198.51.100.5")
    _assert_data(data, marker="198.51.100.5")


def test_crowdsec_shell_defers_and_data_loads(db_session):
    _enable(db_session, "crowdsec")
    routes = import_plugin_module("crowdsec", "routes")
    db_session.add(Event(event_time=datetime(2026, 7, 13, 12), event_type="security.ban", plugin="crowdsec", ip="198.51.100.9"))
    db_session.commit()

    shell = _html(routes.crowdsec_page(_req("/crowdsec", hx=False), db=db_session))
    data = _html(routes.crowdsec_page(_req("/crowdsec", hx=True), db=db_session))

    _assert_shell(shell, marker="198.51.100.9")
    _assert_data(data, marker="198.51.100.9")
