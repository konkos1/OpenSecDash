"""Global browser security headers for normal and error responses."""
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "manifest-src 'self'; "
    "worker-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(), usb=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set app-wide headers without weakening route-specific policies."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        return apply_security_headers(request, response)


def apply_security_headers(request: Request, response: Response) -> Response:
    """Apply the shared policy, including to outer 500 error responses."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)

    path = request.url.path
    content_type = response.headers.get("Content-Type", "")
    authenticated_content = getattr(request.state, "auth_enabled", False) and (
        path.startswith("/api/") or content_type.startswith("text/html")
    )
    sensitive_path = (
        path == "/login"
        or path.startswith("/auth/")
        or path.startswith("/account")
        or path.startswith("/settings")
        or path.startswith("/api/settings")
    )
    if authenticated_content or sensitive_path:
        response.headers.setdefault("Cache-Control", "no-store")
    return response
