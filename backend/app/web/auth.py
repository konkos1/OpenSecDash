"""Shared HTTP authentication helpers and request gating."""
from collections.abc import Awaitable, Callable, Generator
from urllib.parse import quote, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.core.template_context import build_template_context
from app.database.dependencies import get_db
from app.database.session import SessionLocal
from app.services.auth import SESSION_LIFETIME_DAYS, auth_enabled, resolve_session
from app.web.templates import templates

SESSION_COOKIE = "osd_session"
ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}
_PUBLIC_PATHS = {"/health", "/ready", "/login", "/sw.js"}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_ORIGIN_PORTS = {"http": 80, "https": 443}


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
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https",
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


def _cross_origin_request(request: Request) -> bool:
    if request.method not in _UNSAFE_METHODS:
        return False
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False
    parsed_origin = urlparse(origin.strip())
    try:
        origin_port = parsed_origin.port
        request_port = request.url.port
    except ValueError:
        return True
    if (
        parsed_origin.scheme not in _DEFAULT_ORIGIN_PORTS
        or parsed_origin.hostname is None
        or parsed_origin.username is not None
        or parsed_origin.password is not None
        or parsed_origin.path not in ("", "/")
        or parsed_origin.params
        or parsed_origin.query
        or parsed_origin.fragment
    ):
        return True
    request_scheme = request.url.scheme.lower()
    return (
        parsed_origin.scheme,
        parsed_origin.hostname.lower(),
        origin_port if origin_port is not None else _DEFAULT_ORIGIN_PORTS[parsed_origin.scheme],
    ) != (
        request_scheme,
        request.url.hostname.lower() if request.url.hostname is not None else None,
        request_port if request_port is not None else _DEFAULT_ORIGIN_PORTS.get(request_scheme),
    )


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
    if _cross_origin_request(request):
        if wants_json(request):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected"})
        return render_forbidden_page(request)

    if _is_public_path(request.url.path):
        return await call_next(request)

    db, dependency_generator, close_db = _auth_database(request)
    try:
        enabled = auth_enabled(db)
        request.state.auth_enabled = enabled
        if not enabled:
            request.state.user = None
        else:
            token = request.cookies.get(SESSION_COOKIE, "")
            user = resolve_session(db, token) if token else None
            if user is None:
                if wants_json(request):
                    return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
                next_path = request.url.path
                if request.url.query:
                    next_path = f"{next_path}?{request.url.query}"
                return RedirectResponse(f"/login?next={quote(next_path, safe='')}", status_code=303)
            request.state.user = user
            if ROLE_ORDER[user.role] < ROLE_ORDER[required_role(request.method, request.url.path)]:
                if wants_json(request):
                    return JSONResponse(status_code=403, content={"detail": "Forbidden"})
                return render_forbidden_page(request)
    finally:
        if dependency_generator is not None:
            dependency_generator.close()
        elif close_db:
            db.close()

    response = await call_next(request)
    if getattr(request.state, "auth_enabled", False):
        response.headers.setdefault("Cache-Control", "no-store")
    return response
