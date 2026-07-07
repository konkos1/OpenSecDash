from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

from app.api import pages
from app.models.assets import Asset
from app.models.core import Insight
from app.models.events import Event
from app.models.settings import Setting
from app.models.systems import System


def _request(path: str):
    return SimpleNamespace(url=SimpleNamespace(path=path))


def test_ip_explorer_dedupes_insights_by_type(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.add_all(
        [
            Insight(timestamp=datetime(2026, 1, 1, 12, 0), type="web.scan", title="Old scan", ip="8.8.8.8"),
            Insight(timestamp=datetime(2026, 1, 1, 12, 5), type="web.scan", title="New scan", ip="8.8.8.8"),
            Insight(timestamp=datetime(2026, 1, 1, 12, 6), type="web.login", title="Login", ip="8.8.8.8"),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.ip_explorer_page("8.8.8.8", cast(Any, _request("/ip/8.8.8.8")), db=db_session)

    assert [(insight.type, insight.title) for insight in captured["insights"]] == [
        ("web.login", "Login"),
        ("web.scan", "New scan"),
    ]


def test_ip_explorer_cidr_range_shows_ipv4_events_and_insights_for_range(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.add_all(
        [
            Event(event_time=datetime(2026, 1, 1, 12, 0), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="192.0.2.10"),
            Event(event_time=datetime(2026, 1, 1, 12, 1), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="192.0.2.20"),
            Event(event_time=datetime(2026, 1, 1, 12, 2), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="198.51.100.10"),
            Insight(timestamp=datetime(2026, 1, 1, 12, 3), type="web.scan", title="In range insight", ip="192.0.2.30"),
            Insight(timestamp=datetime(2026, 1, 1, 12, 4), type="web.scan", title="Outside insight", ip="198.51.100.20"),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.ip_explorer_page("192.0.2.0/24", cast(Any, _request("/ip/192.0.2.0/24")), db=db_session)

    assert [event.ip for event in captured["events"]] == ["192.0.2.20", "192.0.2.10"]
    assert [(insight.ip, insight.title) for insight in captured["insights"]] == [("192.0.2.30", "In range insight")]


def test_ip_explorer_cidr_range_shows_ipv6_events_and_insights_for_range(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.add_all(
        [
            Event(event_time=datetime(2026, 1, 1, 12, 0), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="2001:db8::10"),
            Event(event_time=datetime(2026, 1, 1, 12, 1), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="2001:db8::20"),
            Event(event_time=datetime(2026, 1, 1, 12, 2), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="2001:db9::10"),
            Insight(timestamp=datetime(2026, 1, 1, 12, 3), type="web.scan", title="IPv6 in range", ip="2001:db8::30"),
            Insight(timestamp=datetime(2026, 1, 1, 12, 4), type="web.scan", title="IPv6 outside", ip="2001:db9::20"),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.ip_explorer_page("2001:db8::%2F32", cast(Any, _request("/ip/2001:db8::%2F32")), db=db_session)

    assert [event.ip for event in captured["events"]] == ["2001:db8::20", "2001:db8::10"]
    assert [(insight.ip, insight.title) for insight in captured["insights"]] == [("2001:db8::30", "IPv6 in range")]


def test_ip_explorer_cidr_range_includes_overlapping_cidr_events(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.add_all(
        [
            Event(event_time=datetime(2026, 1, 1, 12, 0), source="test", plugin="crowdsec", event_type="security.ban", severity="critical", ip="2001:db8::/64"),
            Event(event_time=datetime(2026, 1, 1, 12, 1), source="test", plugin="crowdsec", event_type="security.ban", severity="critical", ip="2001:db9::/64"),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.ip_explorer_page("2001:db8::%2F32", cast(Any, _request("/ip/2001:db8::%2F32")), db=db_session)

    assert [event.ip for event in captured["events"]] == ["2001:db8::/64"]


def test_asset_page_dedupes_system_and_host_insights_by_type_and_ip(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.json_assets.enabled", value="true"))
    system = System(vmid="100", hostname="apps", system_type="vm")
    db_session.add(system)
    db_session.flush()
    app = Asset(system_id=system.id, name="App", host_url="https://app.example.test", is_active=True)
    db_session.add(app)
    db_session.flush()
    db_session.add_all(
        [
            Insight(timestamp=datetime(2026, 1, 1, 12, 0), type="web.scan", title="Old asset scan", ip="8.8.8.8", asset_id=app.id),
            Insight(timestamp=datetime(2026, 1, 1, 12, 5), type="web.scan", title="Other IP asset scan", ip="1.1.1.1", asset_id=app.id),
            Insight(timestamp=datetime(2026, 1, 1, 12, 6), type="web.scan", title="New asset scan", ip="8.8.8.8", asset_id=app.id),
            Insight(timestamp=datetime(2026, 1, 1, 12, 7), type="web.login", title="Asset login", ip="8.8.8.8", asset_id=app.id),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.asset_page(system.id, cast(Any, _request(f"/assets/system/{system.id}")), db=db_session)

    expected = [
        ("web.login", "8.8.8.8", "Asset login"),
        ("web.scan", "8.8.8.8", "New asset scan"),
        ("web.scan", "1.1.1.1", "Other IP asset scan"),
    ]
    assert [(insight.type, insight.ip, insight.title) for insight in captured["insights"]] == expected
    assert [(insight.type, insight.ip, insight.title) for insight in captured["host_event_sections"][0]["insights"]] == expected
