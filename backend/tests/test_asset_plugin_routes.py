from fastapi import HTTPException

from app.models.settings import Setting
from app.web.guards import plugin_enabled_guard
from conftest import import_plugin_module


def _set(db_session, key: str, value: str) -> None:
    db_session.add(Setting(key=key, value=value))
    db_session.commit()


def _route_paths(router) -> set[str]:
    return {getattr(route, "path", "") for route in router.routes}


def test_proxmox_assets_route_is_plugin_owned_and_guarded(db_session):
    routes = import_plugin_module("proxmox_assets", "routes")

    assert "/assets/proxmox-sync" in _route_paths(routes.router)

    _set(db_session, "plugin.proxmox_assets.enabled", "false")
    guard = plugin_enabled_guard("proxmox_assets")
    try:
        guard(db=db_session)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("disabled proxmox_assets route should be guarded")


def test_json_assets_api_import_source_route_is_plugin_owned_and_guarded(db_session):
    routes = import_plugin_module("json_assets", "routes")

    assert "/assets/import-source" in _route_paths(routes.router)
    assert "/api/assets/import-source" in _route_paths(routes.api_router)

    _set(db_session, "plugin.json_assets.enabled", "false")
    guard = plugin_enabled_guard("json_assets")
    try:
        guard(db=db_session)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("disabled json_assets route should be guarded")
