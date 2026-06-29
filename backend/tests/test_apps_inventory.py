from app.models.assets import Asset
from app.models.settings import Setting
from app.models.systems import System
from app.services.apps_inventory_import import import_apps_inventory
from app.services.apps_inventory_updates import refresh_asset_update


def test_import_apps_inventory_creates_updates_and_marks_missing_assets_inactive(db_session):
    first_result = import_apps_inventory(
        db_session,
        {
            "systems": [
                {
                    "vmid": "100",
                    "hostname": "edge-01",
                    "type": "proxmox-lxc",
                    "apps": [
                        {"name": "traefik", "version": "2.10", "release_url": "https://github.com/traefik/traefik/releases/latest"},
                        {"name": "crowdsec", "version": "1.6"},
                    ],
                }
            ]
        },
    )

    assert first_result == {"systems_created": 1, "assets_created": 2, "assets_updated": 0, "assets_inactive": 0}
    system = db_session.query(System).filter_by(vmid="100").one()
    assert system.hostname == "edge-01"
    assert db_session.query(Asset).filter_by(system_id=system.id, is_active=True).count() == 2

    second_result = import_apps_inventory(
        db_session,
        {
            "systems": [
                {
                    "vmid": "100",
                    "hostname": "edge-renamed",
                    "type": "vm",
                    "apps": [{"name": "traefik", "version": "2.11"}],
                }
            ]
        },
    )

    assert second_result == {"systems_created": 0, "assets_created": 0, "assets_updated": 1, "assets_inactive": 1}
    assert system.hostname == "edge-renamed"
    assert db_session.query(Asset).filter_by(system_id=system.id, name="crowdsec").one().is_active is False


def test_refresh_asset_update_uses_github_release_and_token(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.apps_inventory.github_token", value="secret-token"))
    asset = Asset(
        system_id=1,
        name="OpenSecDash",
        version="v1.0.0",
        release_url="https://github.com/example/opensecdash/releases/latest",
    )
    db_session.add(asset)
    db_session.commit()

    calls = []

    def fake_latest_release(*, repo: str, github_token: str) -> str:
        calls.append((repo, github_token))
        return "v1.1.0"

    monkeypatch.setattr("app.services.apps_inventory_updates.get_latest_github_release", fake_latest_release)

    result = refresh_asset_update(db_session, asset)

    assert result == {"checked": 1, "updated": 1, "failed": 0}
    assert calls == [("example/opensecdash", "secret-token")]
    assert asset.latest_version == "v1.1.0"
    assert asset.update_available is True
    assert asset.last_checked is not None
