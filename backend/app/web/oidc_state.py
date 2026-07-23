"""Short-lived signed cookie state for the OIDC redirect handshake."""
import secrets
from typing import Any

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

OIDC_STATE_COOKIE = "osd_oidc_state"
# Long enough to sign in at the provider with a password manager, short enough
# that an abandoned redirect expires on its own.
OIDC_STATE_MAX_AGE_SECONDS = 600
FLOW_KEY = "osd_oidc_flow"

FLOW_MODE_LOGIN = "login"
FLOW_MODE_LINK = "link"

# The key is generated per process on purpose: this cookie only has to survive
# one redirect, never a restart. A restart invalidates in-flight sign-ins,
# which surfaces as "start the sign-in again" instead of a stored secret that
# would outlive the transaction it protects.
_SIGNING_KEY = secrets.token_urlsafe(32)


def install_oidc_state_middleware(app: FastAPI) -> None:
    """Install the separate signed cookie that carries OIDC transaction state.

    This is deliberately not the ``osd_session`` cookie: it holds no
    OpenSecDash session, no client secret and no provider token, and it is only
    ever written while a sign-in or link flow is running.
    """
    app.add_middleware(
        SessionMiddleware,
        secret_key=_SIGNING_KEY,
        session_cookie=OIDC_STATE_COOKIE,
        max_age=OIDC_STATE_MAX_AGE_SECONDS,
        path="/",
        same_site="lax",
        https_only=True,
    )


def new_flow_state() -> str:
    """Return a fresh cryptographic ``state`` value for one flow."""
    return secrets.token_urlsafe(32)


def store_flow(request: Request, *, state: str, mode: str, next_path: str, user_id: int | None = None) -> None:
    """Remember what this redirect was started for, bound to its state value."""
    request.session[FLOW_KEY] = {"state": state, "mode": mode, "next": next_path, "user_id": user_id}


def read_flow(request: Request, state: str | None) -> dict[str, Any] | None:
    """Return the stored flow, but only for the exact state that started it."""
    flow = request.session.get(FLOW_KEY)
    stored_state = flow.get("state") if isinstance(flow, dict) else None
    if not isinstance(stored_state, str) or not isinstance(state, str) or not state:
        return None
    if not secrets.compare_digest(stored_state, state):
        return None
    return flow


def clear_flow(request: Request) -> None:
    """Drop all transaction values, including Authlib's own state entry."""
    request.session.clear()
