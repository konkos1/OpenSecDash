"""Shared validation for browser navigation and redirect targets."""
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request


def is_safe_local_path(value: object) -> bool:
    """Return whether a value is an unambiguous application-local URL path."""
    if not isinstance(value, str):
        return False
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return False
    parts = urlsplit(value)
    return not parts.scheme and not parts.netloc


def safe_local_path(value: str | None, fallback: str = "/") -> str:
    """Return a stripped local path or a safe local fallback."""
    safe_fallback = fallback if is_safe_local_path(fallback) else "/"
    candidate = (value or "").strip()
    return candidate if is_safe_local_path(candidate) else safe_fallback


def safe_local_redirect_target(request: Request, target: str | None, fallback: str) -> str:
    """Convert a local or same-origin URL into a safe local redirect target."""
    safe_fallback = safe_local_path(fallback)
    candidate = (target or safe_fallback).strip() or safe_fallback
    if "\\" in candidate:
        return safe_fallback

    try:
        parts = urlsplit(candidate)
        request_origin = urlsplit(str(request.url))
    except ValueError:
        return safe_fallback

    if parts.scheme or parts.netloc:
        if parts.scheme != request_origin.scheme or parts.netloc != request_origin.netloc:
            return safe_fallback
        candidate = urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))

    if not is_safe_local_path(candidate):
        return safe_fallback
    parts = urlsplit(candidate)
    return urlunsplit(("", "", parts.path, parts.query, parts.fragment))
