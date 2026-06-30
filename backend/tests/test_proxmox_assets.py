from typing import cast, Any

from app.models.assets import Asset
from app.models.systems import System
from app.services import proxmox_assets
from app.services.proxmox_assets import inspect_proxmox_guest_visibility, parse_opensecdash_notes, proxmox_visibility_message, sync_proxmox_assets


def test_parse_opensecdash_notes_hidden_comment():
    notes = """Human notes.

<!-- opensecdash
apps:
  - name: Traefik
    update_check:
      type: github_release
      repo: traefik/traefik
  - name: Authentik
-->
"""

    apps = parse_opensecdash_notes(notes)

    assert apps == [
        {"name": "Traefik", "update_check": {"type": "github_release", "repo": "traefik/traefik"}},
        {"name": "Authentik"},
    ]


class FakeClusterClient:
    def __init__(self, api_url, token_id, token_secret, verify_tls=True):
        pass

    def get(self, path):
        if path == "/nodes":
            return [{"node": "pve1"}]
        if path == "/cluster/resources":
            return [{"vmid": 104, "name": "proxy-lxc", "node": "pve1", "type": "lxc"}]
        if path == "/nodes/pve1/lxc/104/config":
            return {"description": "<!-- opensecdash\napps:\n  - name: Traefik\n-->"}
        raise AssertionError(f"unexpected path: {path}")


class FakeEmptyClient:
    def get(self, path):
        if path == "/cluster/resources":
            return []
        if path == "/nodes/pve1/qemu":
            return []
        if path == "/nodes/pve1/lxc":
            return []
        return []


class FakeClient:
    def __init__(self, api_url, token_id, token_secret, verify_tls=True):
        pass

    def get(self, path):
        if path == "/nodes":
            return [{"node": "pve1"}]
        if path == "/nodes/pve1/qemu":
            return [{"vmid": 101, "name": "docker-vm"}]
        if path == "/nodes/pve1/lxc":
            return [{"vmid": 104, "name": "proxy-lxc"}]
        if path == "/nodes/pve1/qemu/101/config":
            return {"description": "No app metadata"}
        if path == "/nodes/pve1/lxc/104/config":
            return {"description": "<!-- opensecdash\napps:\n  - name: Traefik\n    update_check:\n      type: github_release\n      repo: traefik/traefik\n-->"}
        return []


def test_sync_proxmox_assets_prefers_cluster_resources(monkeypatch, db_session):
    monkeypatch.setattr(proxmox_assets, "ProxmoxClient", FakeClusterClient)

    result = sync_proxmox_assets(db_session, api_url="https://pve.local:8006", token_id="id", token_secret="secret")

    assert result["systems_created"] == 2
    assert db_session.query(System).filter(System.external_id == "proxmox:pve.local:8006:guest:pve1:104").one().hostname == "proxy-lxc"
    assert db_session.query(Asset).filter(Asset.name == "Traefik").one().external_id == "proxmox:pve.local:8006:guest:pve1:104:app:traefik"


def test_proxmox_visibility_message_explains_empty_guest_lists():
    details = inspect_proxmox_guest_visibility(cast(Any, FakeEmptyClient()), [{"node": "pve1"}])
    message = proxmox_visibility_message(details)

    assert details["nodes"] == 1
    assert details["cluster_raw"] == 0
    assert details["qemu_guests"] == 0
    assert details["lxc_guests"] == 0
    assert "nodes=1" in message
    assert "cluster_guests=0" in message


def test_sync_proxmox_assets_imports_systems_and_apps(monkeypatch, db_session):
    monkeypatch.setattr(proxmox_assets, "ProxmoxClient", FakeClient)

    result = sync_proxmox_assets(db_session, api_url="https://pve.local:8006", token_id="id", token_secret="secret")

    assert result["systems_created"] == 3
    assert db_session.query(System).filter(System.source_plugin == "proxmox_assets").count() == 3
    guest = db_session.query(System).filter(System.external_id == "proxmox:pve.local:8006:guest:pve1:104").one()
    asset = db_session.query(Asset).filter(Asset.source_plugin == "proxmox_assets").one()
    assert asset.system_id == guest.id
    assert asset.name == "Traefik"
    assert asset.external_id == "proxmox:pve.local:8006:guest:pve1:104:app:traefik"
    assert asset.release_url == "https://github.com/traefik/traefik/releases/latest"
