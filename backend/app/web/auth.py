"""Shared HTTP authentication helpers and request gating."""
from collections.abc import Awaitable, Callable, Generator
from urllib.parse import quote, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection
from starlette.responses import Response

from app.core.template_context import build_template_context, get_setting_value
from app.database.dependencies import get_db
from app.database.session import SessionLocal
from app.services.auth import AUTH_HOSTNAME_SETTING, SESSION_LIFETIME_DAYS, auth_enabled, normalize_auth_hostname, resolve_session
from app.web.proxy_headers import (
    PROXY_STATE_EXPLICITLY_TRUSTED,
    PROXY_STATE_FORWARDED_HOST,
    PROXY_STATE_FORWARDED_PORT,
    PROXY_STATE_FORWARDED_PROTO,
)
from app.web.templates import templates

SESSION_COOKIE = "osd_session"
ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}
_OPERATIONAL_PATHS = {"/health", "/ready"}
_PUBLIC_PATHS = {"/login", "/sw.js"}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_ORIGIN_PORTS = {"http": 80, "https": 443}
_HSTS_VALUE = "max-age=31536000"


def wants_json(request: Request) -> bool:
    """Return whether a request expects an API-style JSON response."""
    accept = request.headers.get("accept", "")
    return request.url.path.startswith("/api/") or ("application/json" in accept and "text/html" not in accept)


def set_session_cookie(response, request: Request, token: str) -> None:
    """Set the revocable session cookie with the required browser protections."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_LIFETIME_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def render_forbidden_page(request: Request):
    """Render the standard localized forbidden page for browser requests."""
    db = SessionLocal()
    try:
        context = build_template_context(db, getattr(request.state, "user", None))
    finally:
        db.close()
    title = context["t"]("error.forbidden.title")  # type: ignore[index,operator]
    message = context["t"]("error.forbidden.message")  # type: ignore[index,operator]
    current_user = getattr(request.state, "user", None)
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        status_code=403,
        context={
            **context,
            "title": title,
            "message": message,
            "details": [],
            "current_user": current_user,
            "can_admin": current_user is None or current_user.role == "admin",
        },
    )


def _is_public_path(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith("/static/")


def _forwarded_hostname(value: str) -> tuple[str, int] | None:
    parsed = urlparse(f"//{value.strip()}")
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.hostname is None or parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
        return None
    hostname = normalize_auth_hostname(parsed.hostname)
    if hostname is None:
        return None
    return hostname, port if port is not None else 443


def auth_proxy_error(request: HTTPConnection, hostname: str) -> str | None:
    """Validate the trusted HTTPS/443 proxy boundary for an auth hostname."""
    normalized_hostname = normalize_auth_hostname(hostname)
    if normalized_hostname is None:
        return "invalid_hostname"
    if not getattr(request.state, PROXY_STATE_EXPLICITLY_TRUSTED, False):
        return "proxy_not_configured"
    if getattr(request.state, PROXY_STATE_FORWARDED_PROTO, None) != "https":
        return "https_required"
    if getattr(request.state, PROXY_STATE_FORWARDED_PORT, None) != "443":
        return "https_port_required"
    forwarded_host = getattr(request.state, PROXY_STATE_FORWARDED_HOST, None)
    parsed_forwarded_host = _forwarded_hostname(forwarded_host) if isinstance(forwarded_host, str) else None
    if parsed_forwarded_host is None:
        return "hostname_mismatch"
    if parsed_forwarded_host[1] != 443:
        return "https_port_required"
    if parsed_forwarded_host[0] != normalized_hostname:
        return "hostname_mismatch"
    expected_scheme = "wss" if request.scope["type"] == "websocket" else "https"
    try:
        request_port = request.url.port
    except ValueError:
        return "hostname_mismatch"
    if request.url.scheme != expected_scheme or normalize_auth_hostname(request.url.hostname or "") != normalized_hostname or request_port not in (None, 443):
        return "hostname_mismatch"
    return None


def _header_origin(value: str, *, allow_path: bool) -> tuple[str, str, int] | None:
    parsed = urlparse(value.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in _DEFAULT_ORIGIN_PORTS
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or (not allow_path and (parsed.path not in ("", "/") or parsed.params or parsed.query))
        or parsed.fragment
    ):
        return None
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        port if port is not None else _DEFAULT_ORIGIN_PORTS[parsed.scheme],
    )


def _request_origin(request: Request) -> tuple[str, str, int] | None:
    request_scheme = request.url.scheme.lower()
    request_hostname = request.url.hostname
    try:
        request_port = request.url.port
    except ValueError:
        return None
    if request_scheme not in _DEFAULT_ORIGIN_PORTS or request_hostname is None:
        return None
    return (
        request_scheme,
        request_hostname.lower(),
        request_port if request_port is not None else _DEFAULT_ORIGIN_PORTS[request_scheme],
    )


def _unsafe_request_has_invalid_origin(request: Request) -> bool:
    if request.method not in _UNSAFE_METHODS:
        return False
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return True
    request_origin = _request_origin(request)
    origin = request.headers.get("origin")
    if origin is not None:
        return _header_origin(origin, allow_path=False) != request_origin
    referer = request.headers.get("referer")
    return referer is None or _header_origin(referer, allow_path=True) != request_origin


def websocket_origin_is_valid(websocket: HTTPConnection, hostname: str) -> bool:
    """Return whether a browser WebSocket uses the configured HTTPS origin."""
    normalized_hostname = normalize_auth_hostname(hostname)
    origin = websocket.headers.get("origin")
    return normalized_hostname is not None and origin is not None and _header_origin(origin, allow_path=False) == ("https", normalized_hostname, 443)


def _protect_authenticated_response(response: Response) -> Response:
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Strict-Transport-Security", _HSTS_VALUE)
    return response


def required_role(method: str, path: str) -> str:
    """Return the minimum role required for a request."""
    if (
        path == "/settings"
        or path.startswith("/settings/")
        or path.startswith("/api/settings")
        or path == "/diagnostics/debug-report"
    ):
        return "admin"
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    if (
        path.endswith("/columns")
        or path in ("/auth/logout", "/auth/password", "/account/preferences", "/views", "/dashboard/layout", "/dashboard/layout/reset")
        or path.startswith("/views/")
    ):
        return "viewer"
    return "operator"


def _auth_database(request: Request) -> tuple[Session, Generator[Session, None, None] | None, bool]:
    """Use a dependency override when tests provide one, otherwise open a DB session."""
    override = request.app.dependency_overrides.get(get_db)
    if override is not None:
        provided = override()
        if isinstance(provided, Generator):
            return next(provided), provided, False
        if isinstance(provided, Session):
            return provided, None, False
    return SessionLocal(), None, True


async def auth_gating_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Apply opt-in session authentication before core and plugin routes."""
    if request.url.path in _OPERATIONAL_PATHS:
        return await call_next(request)

    if _unsafe_request_has_invalid_origin(request):
        if wants_json(request):
            return JSONResponse(status_code=403, content={"detail": "Request origin rejected"})
        return render_forbidden_page(request)

    db, dependency_generator, close_db = _auth_database(request)
    try:
        enabled = auth_enabled(db)
        request.state.auth_enabled = enabled
        if not enabled:
            request.state.user = None
        else:
            hostname = get_setting_value(db, AUTH_HOSTNAME_SETTING, "")
            if auth_proxy_error(request, hostname) is not None:
                if wants_json(request):
                    return JSONResponse(status_code=403, content={"detail": "Secure authentication origin required"})
                return render_forbidden_page(request)
            if _is_public_path(request.url.path):
                return _protect_authenticated_response(await call_next(request))
            token = request.cookies.get(SESSION_COOKIE, "")
            user = resolve_session(db, token) if token else None
            if user is None:
                if wants_json(request):
                    return _protect_authenticated_response(JSONResponse(status_code=401, content={"detail": "Not authenticated"}))
                next_path = request.url.path
                if request.url.query:
                    next_path = f"{next_path}?{request.url.query}"
                return _protect_authenticated_response(RedirectResponse(f"/login?next={quote(next_path, safe='')}", status_code=303))
            request.state.user = user
            if ROLE_ORDER[user.role] < ROLE_ORDER[required_role(request.method, request.url.path)]:
                if wants_json(request):
                    return _protect_authenticated_response(JSONResponse(status_code=403, content={"detail": "Forbidden"}))
                return _protect_authenticated_response(render_forbidden_page(request))
    finally:
        if dependency_generator is not None:
            dependency_generator.close()
        elif close_db:
            db.close()

    response = await call_next(request)
    if getattr(request.state, "auth_enabled", False):
        return _protect_authenticated_response(response)
    return response
