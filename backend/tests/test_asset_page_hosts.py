from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

from app.api import pages
from app.models.assets import Asset
from app.models.events import Event
from app.models.settings import Setting
from app.models.systems import System


def test_asset_page_groups_events_by_configured_app_host(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.json_assets.enabled", value="true"))
    system = System(vmid="100", hostname="apps", system_type="vm")
    db_session.add(system)
    db_session.flush()
    vault = Asset(system_id=system.id, name="Vaultwarden", host_url="https://vault.example.com", is_active=True)
    nextcloud = Asset(system_id=system.id, name="Nextcloud", host_url="cloud.example.com", is_active=True)
    db_session.add_all([vault, nextcloud])
    db_session.flush()
    db_session.add_all(
        [
            Event(event_time=datetime(2026, 1, 1, 12, 0), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", hostname="vault.example.com", path="/", asset_id=vault.id),
            Event(event_time=datetime(2026, 1, 1, 12, 1), source="test", plugin="traefik_log", event_type="access.allowed", severity="info", hostname="cloud.example.com", path="/login", asset_id=nextcloud.id),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.asset_page(system.id, cast(Any, SimpleNamespace(url=SimpleNamespace(path=f"/assets/system/{system.id}"))), db=db_session)

    sections = captured["host_event_sections"]
    assert [section["host"] for section in sections] == ["cloud.example.com", "vault.example.com"]
    assert [section["apps"][0].name for section in sections] == ["Nextcloud", "Vaultwarden"]
    assert [[event.path for event in section["events"]] for section in sections] == [["/login"], ["/"]]
