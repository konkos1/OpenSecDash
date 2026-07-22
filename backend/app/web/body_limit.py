from __future__ import annotations

from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.input_limits import MAX_ASSET_INVENTORY_BYTES, MAX_EVENT_REQUEST_BODY_BYTES, MAX_REQUEST_BODY_BYTES


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject oversized write bodies from headers and from the ASGI stream."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _limit_for(self, path: str) -> int:
        if path == "/api/events":
            return min(self.max_bytes, MAX_EVENT_REQUEST_BODY_BYTES)
        if path == "/api/assets/import":
            return min(self.max_bytes, MAX_ASSET_INVENTORY_BYTES + 64 * 1024)
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(str(scope.get("path", "")))
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = 0
        if content_length > limit:
            await self._send_too_large(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._send_too_large(scope, receive, send)

    async def _send_too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        accepts_json = path.startswith("/api/") or b"application/json" in headers.get(b"accept", b"")
        response = (
            JSONResponse({"detail": "Request body is too large"}, status_code=413)
            if accepts_json
            else HTMLResponse(
                "<!doctype html><html><head><title>Request too large</title></head>"
                "<body><h1>Request too large</h1><p>The submitted data exceeds the allowed size.</p></body></html>",
                status_code=413,
            )
        )
        await response(scope, receive, send)
