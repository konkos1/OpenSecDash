from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

from app.api import pages
from app.models.assets import Asset
from app.models.core import Insight
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
