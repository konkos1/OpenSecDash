"""OIDC sign-in, callback handling and self-service account linking."""
import logging

import httpx
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.starlette_client import StarletteOAuth2App
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from joserfc.errors import JoseError
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.database.dependencies import get_db
from app.models.users import User
from app.services.auth import (
    AUTH_HOSTNAME_SETTING,
    AUTH_METHOD_OIDC,
    auth_enabled,
    cleanup_expired_sessions,
    create_session,
    find_external_identity,
    find_user_external_identity,
    link_external_identity,
    resolve_session,
)
from app.services.oidc import (
    CLOCK_SKEW_SECONDS,
    MAX_SUBJECT_LENGTH,
    OidcConfig,
    OidcConfigurationError,
    build_oauth_client,
    callback_url,
    id_token_claims_options,
    load_config,
    load_provider_metadata,
)
from app.web.auth import SESSION_COOKIE, auth_proxy_error, set_session_cookie
from app.web.oidc_state import (
    FLOW_MODE_LINK,
    FLOW_MODE_LOGIN,
    clear_flow,
    new_flow_state,
    read_flow,
    store_flow,
)
from app.web.redirects import safe_local_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oidc"])

# Anything the provider or the network can raise on the way to a verified ID
# token. They all end in the same generic message: the distinction only ever
# reaches the log, never the browser.
_PROVIDER_FAILURES = (AuthlibBaseError, JoseError, httpx.HTTPError, ValueError, KeyError)


def _login_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/login?oidc_error={code}", status_code=303)


def _account_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/account?oidc_error={code}", status_code=303)


def _account_notice(code: str) -> RedirectResponse:
    return RedirectResponse(f"/account?oidc_notice={code}", status_code=303)


def _boundary_is_valid(request: Request, db: Session) -> bool:
    """Return whether the request arrived on the validated HTTPS/443 boundary."""
    return auth_proxy_error(request, get_setting_value(db, AUTH_HOSTNAME_SETTING, "")) is None


async def _provider(db: Session) -> tuple[OidcConfig, StarletteOAuth2App] | str:
    """Return the configured provider client, or a stable error code."""
    config = load_config(db)
    if not config.enabled or not config.complete or not config.issuer:
        return "unavailable"
    try:
        metadata = await load_provider_metadata(config)
    except OidcConfigurationError as exc:
        logger.warning("OIDC provider is not usable code=%s", exc.code)
        return "provider_error"
    return config, build_oauth_client(config, metadata)


async def _authorization_redirect(
    request: Request,
    db: Session,
    *,
    mode: str,
    next_path: str,
    user_id: int | None = None,
) -> RedirectResponse | str:
    """Start one authorization code flow, or return a stable error code."""
    target = callback_url(get_setting_value(db, AUTH_HOSTNAME_SETTING, ""))
    if target is None:
        return "unavailable"
    provider = await _provider(db)
    if isinstance(provider, str):
        return provider
    _config, client = provider
    state = new_flow_state()
    store_flow(request, state=state, mode=mode, next_path=next_path, user_id=user_id)
    # Only the fixed callback URL and our own state are passed on: no scope,
    # prompt or redirect URI may come from the user's query string.
    return await client.authorize_redirect(request, target, state=state)


@router.get("/auth/oidc/login")
async def oidc_login(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if not auth_enabled(db):
        # Nothing to sign in to while authentication is off or break-glass is
        # active, so no request leaves the container.
        return RedirectResponse("/", status_code=303)
    if not _boundary_is_valid(request, db):
        return _login_error("unavailable")
    result = await _authorization_redirect(request, db, mode=FLOW_MODE_LOGIN, next_path=safe_local_path(next))
    return _login_error(result) if isinstance(result, str) else result


@router.post("/account/oidc/link")
async def oidc_link(request: Request, db: Session = Depends(get_db)):
    if not auth_enabled(db):
        return RedirectResponse("/", status_code=303)
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if not _boundary_is_valid(request, db):
        return _account_error("unavailable")
    result = await _authorization_redirect(
        request,
        db,
        mode=FLOW_MODE_LINK,
        next_path="/account",
        user_id=user.id,
    )
    return _account_error(result) if isinstance(result, str) else result


def _verified_identity(config: OidcConfig, claims: dict[str, object]) -> tuple[str, str] | None:
    """Return the issuer and subject to log in with, if the claims are usable."""
    issuer = claims.get("iss")
    subject = claims.get("sub")
    if not isinstance(issuer, str) or issuer != config.issuer:
        return None
    if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT_LENGTH:
        return None
    return issuer, subject


def _complete_login(request: Request, db: Session, issuer: str, subject: str, next_path: str) -> RedirectResponse:
    """Turn a verified external identity into a normal OpenSecDash session."""
    identity = find_external_identity(db, issuer, subject)
    if identity is None:
        # Unknown identities are rejected without a hint that they are unknown,
        # and without creating anything. Just-in-time users are a later step.
        return _login_error("login_failed")
    user = db.query(User).filter(User.id == identity.user_id).first()
    if user is None or not user.is_active:
        return _login_error("login_failed")

    now = utc_now().replace(tzinfo=None)
    user.last_login_at = now
    identity.last_login_at = now
    cleanup_expired_sessions(db)
    token = create_session(db, user, AUTH_METHOD_OIDC)
    db.commit()
    response = RedirectResponse(safe_local_path(next_path), status_code=303)
    set_session_cookie(response, request, token)
    return response


def _complete_link(request: Request, db: Session, user_id: object, issuer: str, subject: str) -> RedirectResponse:
    """Link a verified external identity to the user who started the flow."""
    # The OIDC proof alone is not enough: the same browser must still hold a
    # valid session for exactly the local user that started the link.
    user = resolve_session(db, request.cookies.get(SESSION_COOKIE, ""))
    if user is None or not isinstance(user_id, int) or user.id != user_id:
        return _account_error("session_expired")

    existing = find_user_external_identity(db, user.id)
    if existing is not None:
        if existing.issuer == issuer and existing.subject == subject:
            return _account_notice("already_linked")
        # Silently replacing a link would let one provider account take over an
        # account that another one can still sign in with.
        return _account_error("other_identity")

    result = link_external_identity(db, user.id, issuer, subject)
    if isinstance(result, str):
        return _account_error("identity_taken" if result == "identity_taken" else "other_identity")
    db.commit()
    return _account_notice("linked")


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    if not auth_enabled(db):
        clear_flow(request)
        return RedirectResponse("/", status_code=303)
    if not _boundary_is_valid(request, db):
        clear_flow(request)
        return _login_error("unavailable")

    flow = read_flow(request, request.query_params.get("state"))
    if flow is None:
        # No state, a replayed state or a restarted process: all of them mean
        # the transaction is gone and the sign-in has to be started again.
        clear_flow(request)
        return _login_error("session_expired")
    is_link = flow.get("mode") == FLOW_MODE_LINK
    fail = _account_error if is_link else _login_error

    provider = await _provider(db)
    if isinstance(provider, str):
        clear_flow(request)
        return fail(provider)
    config, client = provider

    try:
        token = await client.authorize_access_token(
            request,
            claims_options=id_token_claims_options(config),
            leeway=CLOCK_SKEW_SECONDS,
        )
    except _PROVIDER_FAILURES as exc:
        # Only the exception type is logged: codes, tokens and claims never
        # belong in a log line.
        logger.warning("OIDC callback was rejected reason=%s", type(exc).__name__)
        clear_flow(request)
        return fail("provider_error")

    claims = token.get("userinfo") if isinstance(token, dict) else None
    identity = _verified_identity(config, claims) if isinstance(claims, dict) else None
    if identity is None:
        logger.warning("OIDC callback delivered an unusable ID token")
        clear_flow(request)
        return fail("provider_error")
    issuer, subject = identity

    clear_flow(request)
    if is_link:
        return _complete_link(request, db, flow.get("user_id"), issuer, subject)
    return _complete_login(request, db, issuer, subject, str(flow.get("next") or "/"))
