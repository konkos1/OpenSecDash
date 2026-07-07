from datetime import timedelta

import requests

from app.core.time import utc_now
from app.models.assets import Asset
from app.models.settings import Setting
from app.models.systems import System
from app.api.pages import asset_last_seen_stale, asset_system_matches_search
from app.services.asset_updates import refresh_asset_update, refresh_asset_updates
from conftest import import_plugin_module

json_assets_importer = import_plugin_module("json_assets", "services.importer")
import_json_assets = json_assets_importer.import_json_assets


def test_import_json_assets_creates_updates_and_marks_missing_assets_inactive(db_session):
    first_result = import_json_assets(
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
    assert system.source_plugin == "json_assets"
    assert system.external_id == "json_assets:system:100"
    traefik = db_session.query(Asset).filter_by(system_id=system.id, name="traefik").one()
    assert traefik.source_plugin == "json_assets"
    assert traefik.external_id == "json_assets:system:100:app:traefik"
    assert db_session.query(Asset).filter_by(system_id=system.id, is_active=True).count() == 2

    second_result = import_json_assets(
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


def test_asset_search_matches_system_and_app_fields(db_session):
    system = System(vmid="104", hostname="proxy-lxc", system_type="lxc", source_plugin="proxmox_assets", external_id="proxmox:pve:guest:pve1:104")
    db_session.add(system)
    db_session.flush()
    asset = Asset(system_id=system.id, name="Traefik", version="v3.0", release_url="https://github.com/traefik/traefik/releases/latest", host_url="edge.example.test")
    db_session.add(asset)
    db_session.commit()

    assert asset_system_matches_search(system, [asset], "proxy") is True
    assert asset_system_matches_search(system, [asset], "traefik github") is True
    assert asset_system_matches_search(system, [asset], "edge.example") is True
    assert asset_system_matches_search(system, [asset], "proxmox_assets") is False
    assert asset_system_matches_search(system, [asset], "authentik") is False


def test_asset_last_seen_stale_uses_source_thresholds():
    now = utc_now().replace(tzinfo=None)

    assert asset_last_seen_stale(now - timedelta(hours=25), "proxmox_assets", now) is True
    assert asset_last_seen_stale(now - timedelta(hours=23), "proxmox_assets", now) is False
    assert asset_last_seen_stale(now - timedelta(days=8), "json_assets", now) is True
    assert asset_last_seen_stale(now - timedelta(days=6), "json_assets", now) is False
    assert asset_last_seen_stale(None, "proxmox_assets", now) is True


def test_asset_sources_are_available_for_filtering(db_session):
    db_session.add_all([
        System(vmid="100", hostname="json-host", system_type="vm", source_plugin="json_assets", external_id="json_assets:system:100"),
        System(vmid="101", hostname="pve-host", system_type="lxc", source_plugin="proxmox_assets", external_id="proxmox:pve:guest:pve1:101"),
    ])
    db_session.commit()

    sources = [value for (value,) in db_session.query(System.source_plugin).distinct().order_by(System.source_plugin).all() if value]

    assert sources == ["json_assets", "proxmox_assets"]


def test_refresh_asset_update_stores_latest_without_installed_version(monkeypatch, db_session):
    asset = Asset(
        system_id=1,
        name="Traefik",
        version="",
        release_url="https://github.com/traefik/traefik/releases/latest",
    )
    db_session.add(asset)
    db_session.commit()

    monkeypatch.setattr("app.services.asset_updates.get_latest_github_release", lambda *, repo, github_token: "v3.0.0")

    result = refresh_asset_update(db_session, asset)

    assert result == {"checked": 1, "updated": 1, "failed": 0}
    assert asset.latest_version == "v3.0.0"
    assert asset.update_available is False
    assert asset.last_checked is not None


def test_refresh_asset_updates_caches_repositories_per_run(monkeypatch, db_session):
    db_session.add_all([
        Asset(system_id=1, name="Traefik A", version="v2.0", release_url="https://github.com/traefik/traefik/releases/latest"),
        Asset(system_id=1, name="Traefik B", version="v2.1", release_url="https://github.com/traefik/traefik/releases/latest"),
        Asset(system_id=1, name="CrowdSec", version="v1.0", release_url="https://github.com/crowdsecurity/crowdsec/releases/latest"),
    ])
    db_session.commit()
    calls = []

    def fake_latest_release(*, repo: str, github_token: str) -> str:
        calls.append(repo)
        return "v3.0.0" if repo == "traefik/traefik" else "v2.0.0"

    monkeypatch.setattr("app.services.asset_updates.get_latest_github_release", fake_latest_release)

    result = refresh_asset_updates(db_session)

    assert result == {"checked": 3, "updated": 3, "failed": 0, "failed_assets": [], "failed_reasons": []}
    assert sorted(calls) == ["crowdsecurity/crowdsec", "traefik/traefik"]


def test_refresh_asset_updates_reports_failed_asset_names(monkeypatch, db_session):
    db_session.add(Asset(system_id=1, name="Broken App", version="v1.0", release_url="https://github.com/example/missing/releases/latest"))
    db_session.commit()

    def fake_latest_release(*, repo: str, github_token: str) -> str:
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr("app.services.asset_updates.get_latest_github_release", fake_latest_release)

    result = refresh_asset_updates(db_session)

    assert result == {
        "checked": 1,
        "updated": 0,
        "failed": 1,
        "failed_assets": ["Broken App (example/missing: GitHub unavailable)"],
        "failed_reasons": ["GitHub unavailable"],
    }


def test_refresh_asset_updates_normalizes_github_rate_limit(monkeypatch, db_session):
    db_session.add(Asset(system_id=1, name="Rate Limited App", version="v1.0", release_url="https://github.com/example/rate-limited/releases/latest"))
    db_session.commit()

    def fake_latest_release(*, repo: str, github_token: str) -> str:
        response = requests.Response()
        response.status_code = 403
        response.headers["X-RateLimit-Remaining"] = "0"
        response._content = b'{"message":"API rate limit exceeded"}'
        raise requests.HTTPError("403 Client Error: Forbidden", response=response)

    monkeypatch.setattr("app.services.asset_updates.get_latest_github_release", fake_latest_release)

    result = refresh_asset_updates(db_session)

    assert result["failed_reasons"] == ["GitHub rate limit exceeded"]
    assert result["failed_assets"] == ["Rate Limited App (example/rate-limited: GitHub rate limit exceeded)"]


def test_refresh_asset_update_uses_github_release_and_token(monkeypatch, db_session):
    db_session.add(Setting(key="asset_updates.github_token", value="secret-token"))
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

    monkeypatch.setattr("app.services.asset_updates.get_latest_github_release", fake_latest_release)

    result = refresh_asset_update(db_session, asset)

    assert result == {"checked": 1, "updated": 1, "failed": 0}
    assert calls == [("example/opensecdash", "secret-token")]
    assert asset.latest_version == "v1.1.0"
    assert asset.update_available is True
    assert asset.last_checked is not None
