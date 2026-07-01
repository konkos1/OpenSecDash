from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.assets import Asset
from app.models.systems import System

SOURCE_PLUGIN = "proxmox_assets"
NOTES_RE = re.compile(r"<!--\s*opensecdash\s*(.*?)\s*-->", re.IGNORECASE | re.DOTALL)
logger = logging.getLogger(__name__)


def normalize_proxmox_host(api_url: str) -> str:
    parsed = urlparse(api_url if "://" in api_url else f"https://{api_url}")
    return parsed.netloc or parsed.path.strip("/")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "unnamed"


def github_release_url(repo: str) -> str:
    return f"https://github.com/{repo.strip().strip('/')}/releases/latest"


def parse_opensecdash_notes(notes: str | None) -> list[dict[str, Any]]:
    if not notes:
        return []
    match = NOTES_RE.search(notes)
    if not match:
        return []
    apps: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_update_check = False
    for raw_line in match.group(1).splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "apps:":
            continue
        if stripped.startswith("- "):
            if current and current.get("name"):
                apps.append(current)
            current = {}
            in_update_check = False
            rest = stripped[2:].strip()
            if rest.startswith("name:"):
                current["name"] = rest.split(":", 1)[1].strip().strip('"\'')
            continue
        if current is None:
            continue
        if stripped == "update_check:":
            current.setdefault("update_check", {})
            in_update_check = True
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"\'')
        if in_update_check and key in {"type", "repo"}:
            current.setdefault("update_check", {})[key] = value
        elif key == "name":
            current["name"] = value
    if current and current.get("name"):
        apps.append(current)
    return apps


class ProxmoxClient:
    def __init__(self, api_url: str, token_id: str, token_secret: str, verify_tls: bool = True, timeout: int = 15) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"PVEAPIToken={token_id}={token_secret}"})

    def get(self, path: str) -> Any:
        response = self.session.get(f"{self.api_url}/api2/json{path}", timeout=self.timeout, verify=self.verify_tls)
        response.raise_for_status()
        return response.json().get("data", [])


def _guest_kind(value: object) -> str | None:
    kind = str(value or "").strip().lower()
    if kind in {"qemu", "vm"}:
        return "qemu"
    if kind in {"lxc", "ct"}:
        return "lxc"
    return None


def _system_type_for_kind(kind: str) -> str:
    return "vm" if kind == "qemu" else "lxc"


def inspect_proxmox_guest_visibility(client: ProxmoxClient, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Inspect guest visibility and include endpoint-level diagnostics."""
    details: dict[str, Any] = {
        "nodes": len(nodes),
        "cluster_endpoint": "/cluster/resources",
        "cluster_raw": 0,
        "cluster_guests": 0,
        "cluster_error": "",
        "qemu_guests": 0,
        "lxc_guests": 0,
        "fallback_errors": [],
        "guests": [],
    }
    try:
        # Proxmox resource types are "qemu" and "lxc"; "type=vm" is not a
        # valid filter and returns an empty list on PVE 9.x. Query all cluster
        # resources and filter client-side.
        resources = client.get("/cluster/resources")
        resources = resources if isinstance(resources, list) else []
        details["cluster_raw"] = len(resources)
        guests = [item for item in resources if isinstance(item, dict) and _guest_kind(item.get("type")) and item.get("node") and item.get("vmid")]
        details["cluster_guests"] = len(guests)
        if guests:
            details["guests"] = guests
            return details
        logger.info("Proxmox cluster resources returned no guests; falling back to per-node guest endpoints")
    except Exception as exc:
        details["cluster_error"] = str(exc)
        logger.info("Proxmox cluster resources unavailable; falling back to per-node guest endpoints: %s", exc)

    guests: list[dict[str, Any]] = []
    for node in nodes:
        node_name = str(node.get("node") or "").strip()
        if not node_name:
            continue
        for kind in ["qemu", "lxc"]:
            try:
                node_guests = client.get(f"/nodes/{node_name}/{kind}")
                node_guests = node_guests if isinstance(node_guests, list) else []
                details[f"{kind}_guests"] = int(details[f"{kind}_guests"]) + len(node_guests)
                for guest in node_guests:
                    if isinstance(guest, dict):
                        guests.append({**guest, "node": node_name, "type": kind})
            except Exception as exc:
                message = f"{node_name}/{kind}: {exc}"
                details["fallback_errors"].append(message)
                logger.warning("Could not list Proxmox %s guests on node %s: %s", kind, node_name, exc)
    details["guests"] = guests
    return details


def proxmox_visibility_message(details: dict[str, Any]) -> str:
    message = (
        f"nodes={details.get('nodes', 0)}; "
        f"cluster_endpoint={details.get('cluster_endpoint', '/cluster/resources')}; "
        f"cluster_raw={details.get('cluster_raw', 0)}; "
        f"cluster_guests={details.get('cluster_guests', 0)}; "
        f"qemu_guests={details.get('qemu_guests', 0)}; "
        f"lxc_guests={details.get('lxc_guests', 0)}"
    )
    if details.get("cluster_error"):
        message += f"; cluster_error={details['cluster_error']}"
    errors = details.get("fallback_errors") or []
    if errors:
        message += "; fallback_errors=" + " | ".join(str(error) for error in errors)
    return message


def _guest_resources(client: ProxmoxClient, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return guests from Proxmox using cluster resources with per-node fallback."""
    details = inspect_proxmox_guest_visibility(client, nodes)
    guests = details.get("guests")
    return guests if isinstance(guests, list) else []


def sync_proxmox_assets(db: Session, *, api_url: str, token_id: str, token_secret: str, verify_tls: bool = True) -> dict[str, int]:
    client = ProxmoxClient(api_url, token_id, token_secret, verify_tls=verify_tls)
    source_host = normalize_proxmox_host(api_url)
    now = utc_now().replace(tzinfo=None)
    systems_created = systems_updated = assets_created = assets_updated = assets_inactive = 0
    seen_asset_ids: set[str] = set()

    nodes = [item for item in client.get("/nodes") if isinstance(item, dict)]
    for node in nodes:
        node_name = str(node.get("node") or "").strip()
        if not node_name:
            continue
        node_external_id = f"proxmox:{source_host}:node:{node_name}"
        system = db.query(System).filter(System.source_plugin == SOURCE_PLUGIN, System.external_id == node_external_id).first()
        if system is None:
            system = System(vmid=node_name, hostname=node_name, system_type="proxmox-node", source_plugin=SOURCE_PLUGIN, external_id=node_external_id, last_seen=now)
            db.add(system); db.flush(); systems_created += 1
        else:
            system.hostname = node_name; system.system_type = "proxmox-node"; system.last_seen = now; systems_updated += 1


    for guest in _guest_resources(client, nodes):
        kind = _guest_kind(guest.get("type"))
        node_name = str(guest.get("node") or "").strip()
        vmid = str(guest.get("vmid") or "").strip()
        if not kind or not node_name or not vmid:
            continue
        system_type = _system_type_for_kind(kind)
        name = str(guest.get("name") or f"{system_type}-{vmid}").strip()
        # `systems.vmid` is unique for historical JSON Assets data. Include
        # the Proxmox node in the displayed VMID to avoid collisions when a user
        # runs JSON Assets and Proxmox Assets in parallel or during migration.
        display_vmid = f"{node_name}:{vmid}"
        external_id = f"proxmox:{source_host}:guest:{node_name}:{vmid}"
        guest_system = db.query(System).filter(System.source_plugin == SOURCE_PLUGIN, System.external_id == external_id).first()
        if guest_system is None:
            guest_system = System(vmid=display_vmid, hostname=name, system_type=system_type, source_plugin=SOURCE_PLUGIN, external_id=external_id, last_seen=now)
            db.add(guest_system); db.flush(); systems_created += 1
        else:
            guest_system.vmid = display_vmid; guest_system.hostname = name; guest_system.system_type = system_type; guest_system.last_seen = now; systems_updated += 1

        notes = ""
        try:
            config = client.get(f"/nodes/{node_name}/{kind}/{vmid}/config")
            if isinstance(config, dict):
                notes = str(config.get("description") or "")
        except Exception as exc:
            logger.warning("Could not read Proxmox config for %s %s on node %s: %s", kind, vmid, node_name, exc)
        for app in parse_opensecdash_notes(notes):
            app_name = str(app.get("name") or "").strip()
            if not app_name:
                continue
            asset_external_id = f"{external_id}:app:{slug(app_name)}"
            seen_asset_ids.add(asset_external_id)
            asset = db.query(Asset).filter(Asset.source_plugin == SOURCE_PLUGIN, Asset.external_id == asset_external_id).first()
            raw_update_check = app.get("update_check")
            update_check: dict[str, Any] = raw_update_check if isinstance(raw_update_check, dict) else {}
            update_type = str(update_check.get("type") or "github_release")
            update_repo = str(update_check.get("repo") or "")
            release_url = github_release_url(update_repo) if update_type == "github_release" and update_repo else None
            if asset is None:
                asset = Asset(system_id=guest_system.id, name=app_name, version="", release_url=release_url, update_check_type=update_type, source_plugin=SOURCE_PLUGIN, external_id=asset_external_id, is_active=True, last_seen=now)
                db.add(asset); assets_created += 1
            else:
                asset.system_id = guest_system.id
                asset.name = app_name
                asset.is_active = True
                asset.last_seen = now
                if release_url:
                    asset.release_url = release_url
                    asset.update_check_type = update_type or asset.update_check_type or "github_release"
                assets_updated += 1

    for asset in db.query(Asset).filter(Asset.source_plugin == SOURCE_PLUGIN).all():
        if asset.external_id not in seen_asset_ids and asset.is_active:
            asset.is_active = False
            assets_inactive += 1
    db.commit()
    return {"systems_created": systems_created, "systems_updated": systems_updated, "assets_created": assets_created, "assets_updated": assets_updated, "assets_inactive": assets_inactive}
