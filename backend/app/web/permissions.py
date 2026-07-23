"""Declarative role requirements for every OpenSecDash HTTP route."""
from dataclasses import dataclass
from re import Pattern

from starlette.routing import compile_path

Role = str
RouteCategory = str


@dataclass(frozen=True)
class RoutePermission:
    method: str
    path: str
    owner: str
    category: RouteCategory
    role: Role
    path_regex: Pattern[str]


def _permission(method: str, path: str, owner: str, category: RouteCategory, role: Role) -> RoutePermission:
    path_regex, _path_format, _param_convertors = compile_path(path)
    return RoutePermission(method, path, owner, category, role, path_regex)


def _permissions(owner: str, category: RouteCategory, role: Role, *routes: tuple[str, str]) -> list[RoutePermission]:
    return [_permission(method, path, owner, category, role) for method, path in routes]


ROUTE_PERMISSIONS = (
    *_permissions(
        "core",
        "public",
        "public",
        ("GET", "/login"),
        ("POST", "/login"),
        ("GET", "/auth/oidc/login"),
        ("GET", "/auth/oidc/callback"),
        ("GET", "/manifest.webmanifest"),
        ("GET", "/sw.js"),
        ("GET", "/health"),
        ("GET", "/ready"),
    ),
    *_permissions(
        "core",
        "read-only",
        "viewer",
        ("GET", "/"),
        ("GET", "/fragments/backlog-banner"),
        ("GET", "/search"),
        ("GET", "/rollups"),
        ("GET", "/events"),
        ("GET", "/ip/{ip:path}"),
        ("GET", "/assets"),
        ("GET", "/assets/system/{system_id}"),
        ("GET", "/assets/app/{asset_id}"),
        ("GET", "/diagnostics"),
        ("GET", "/notifications"),
        ("GET", "/account"),
        ("GET", "/api/events"),
        ("GET", "/api/events/{event_id}"),
        ("GET", "/api/actions"),
        ("GET", "/api/actions/available"),
        ("GET", "/api/assets"),
        ("GET", "/instance/logo"),
        ("GET", "/instance/favicon"),
        ("GET", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/openapi.json"),
        ("GET", "/redoc"),
    ),
    *_permissions(
        "core",
        "personal-preference",
        "viewer",
        ("POST", "/views"),
        ("POST", "/views/{view_id}/delete"),
        ("POST", "/dashboard/layout"),
        ("POST", "/dashboard/layout/reset"),
        ("POST", "/events/columns"),
        ("POST", "/account/preferences"),
        ("POST", "/account/oidc/link"),
        ("POST", "/auth/password"),
        ("POST", "/auth/logout"),
    ),
    *_permissions(
        "core",
        "operational",
        "operator",
        ("POST", "/api/actions"),
        ("POST", "/actions/ip"),
        ("POST", "/api/assets/refresh-updates"),
        ("POST", "/notifications/test"),
        ("POST", "/assets/{asset_id}/mqtt"),
        ("POST", "/assets/refresh-updates"),
    ),
    *_permissions(
        "core",
        "administration",
        "admin",
        ("POST", "/api/events"),
        ("GET", "/api/settings"),
        ("GET", "/api/settings/{key}"),
        ("PUT", "/api/settings/{key}"),
        ("GET", "/settings"),
        ("GET", "/diagnostics/debug-report"),
        ("POST", "/settings/core"),
        ("POST", "/settings/notifications"),
        ("POST", "/settings/asset-updates"),
        ("POST", "/settings/plugins/{plugin_id}"),
        ("POST", "/settings/branding"),
        ("POST", "/settings/branding/remove"),
        ("POST", "/settings/auth/enable"),
        ("POST", "/settings/auth/hostname"),
        ("POST", "/settings/auth/disable"),
        ("POST", "/settings/auth/oidc"),
        ("POST", "/settings/auth/oidc/enable"),
        ("POST", "/settings/auth/oidc/disable"),
        ("POST", "/settings/auth/oidc/secret/delete"),
        ("POST", "/settings/auth/oidc/jit"),
        ("POST", "/settings/users/create"),
        ("POST", "/settings/users/{user_id}/role"),
        ("POST", "/settings/users/{user_id}/password"),
        ("POST", "/settings/users/password"),
        ("POST", "/settings/users/{user_id}/toggle"),
        ("POST", "/settings/users/{user_id}/delete"),
        ("POST", "/notifications/rules"),
        ("POST", "/assets/{asset_id}/metadata"),
        ("POST", "/assets/cleanup-inactive"),
    ),
    *_permissions("traefik_log", "read-only", "viewer", ("GET", "/access")),
    *_permissions("traefik_log", "personal-preference", "viewer", ("POST", "/access/columns")),
    *_permissions("crowdsec", "read-only", "viewer", ("GET", "/crowdsec")),
    *_permissions("crowdsec", "operational", "operator", ("POST", "/crowdsec/decisions/refresh")),
    *_permissions("proxmox_assets", "operational", "operator", ("POST", "/assets/proxmox-sync")),
    *_permissions("mqtt", "operational", "operator", ("POST", "/assets/mqtt-publish")),
    *_permissions(
        "json_assets",
        "administration",
        "admin",
        ("POST", "/assets/import-source"),
        ("POST", "/api/assets/import"),
        ("POST", "/api/assets/import-source"),
    ),
)


def required_role(method: str, path: str) -> Role:
    """Return the declared role, failing closed for unknown writing routes."""
    normalized_method = method.upper()
    for permission in ROUTE_PERMISSIONS:
        if permission.method == normalized_method and permission.path_regex.fullmatch(path):
            return permission.role
    if normalized_method in {"GET", "HEAD", "OPTIONS"}:
        return "viewer"
    return "admin"


def route_permission_inventory() -> tuple[RoutePermission, ...]:
    """Return the auditable Core and plugin route registry."""
    return ROUTE_PERMISSIONS
