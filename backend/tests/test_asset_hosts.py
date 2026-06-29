from app.models.assets import Asset
from app.models.events import Event
from app.models.systems import System
from app.services.asset_hosts import normalize_asset_host, sync_asset_host_events


def test_asset_host_normalization_accepts_urls_and_plain_hosts():
    assert normalize_asset_host("https://App.Example.COM/path") == "app.example.com"
    assert normalize_asset_host("app.example.com.") == "app.example.com"
    assert normalize_asset_host(" app.example.com/admin ") == "app.example.com"
    assert normalize_asset_host("") is None


def test_sync_asset_host_events_attaches_and_detaches_derived_links(db_session):
    system = System(vmid="100", hostname="host", system_type="vm")
    db_session.add(system)
    db_session.flush()
    asset = Asset(system_id=system.id, name="Vaultwarden", host_url="https://vault.example.com", is_active=True)
    matching = Event(event_type="access.allowed", severity="info", plugin="traefik_log", hostname="vault.example.com")
    other = Event(event_type="access.allowed", severity="info", plugin="traefik_log", hostname="other.example.com")
    db_session.add_all([asset, matching, other])
    db_session.commit()

    assert sync_asset_host_events(db_session, asset) == 1
    assert matching.asset_id == asset.id
    assert other.asset_id is None

    asset.host_url = "new.example.com"
    assert sync_asset_host_events(db_session, asset) == 1
    assert matching.asset_id is None
