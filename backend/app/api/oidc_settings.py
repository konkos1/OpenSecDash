"""Admin routes for the single generic OIDC provider configuration."""
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.database.dependencies import get_db
from app.models.users import UserSession
from app.services.auth import (
    AUTH_HOSTNAME_SETTING,
    AUTH_METHOD_OIDC,
    AUTH_METHOD_PASSWORD,
    active_oidc_admin_count,
    auth_disabled_by_environment,
    auth_enabled,
    delete_sessions_by_auth_method,
    find_user_external_identity,
)
from app.services.oidc import (
    CHECK_STATUS_ERROR,
    CHECK_STATUS_HEALTHY,
    OIDC_CHECK_AT_SETTING,
    OIDC_CHECK_ERROR_SETTING,
    OIDC_CHECK_STATUS_SETTING,
    OIDC_CLIENT_ID_SETTING,
    OIDC_CLIENT_SECRET_SETTING,
    OIDC_DISCOVERY_URL_SETTING,
    OIDC_ENABLED_SETTING,
    OIDC_ISSUER_SETTING,
    OIDC_JIT_ENABLED_SETTING,
    PASSWORD_LOGIN_ENABLED_SETTING,
    OidcConfigurationError,
    check_provider,
    effective_password_login_enabled,
    invalidate_provider_cache,
    load_config,
    oidc_login_available,
    password_login_enabled,
)
from app.web.auth import auth_proxy_error
from app.web.tables import save_setting

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oidc"])


def _error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?oidc_error={code}", status_code=303)


def _notice(code: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?oidc_notice={code}", status_code=303)


def _boundary_error(request: Request, db: Session) -> RedirectResponse | None:
    """Apply the same HTTPS/443/hostname boundary the rest of auth management uses.

    While the break-glass override is active there is no trusted boundary to
    check against, and recovery must stay possible - see the recovery contract
    in the authentication ADR.
    """
    if auth_disabled_by_environment():
        return None
    proxy_error = auth_proxy_error(request, get_setting_value(db, AUTH_HOSTNAME_SETTING, ""))
    if proxy_error is None:
        return None
    # Reuse the existing boundary messages and their diagnostics link.
    return RedirectResponse(f"/settings?auth_error={proxy_error}", status_code=303)


def _enforce_login_methods(db: Session) -> None:
    """Keep at least one sign-in method persisted, inside the caller's transaction.

    Every route in this module ends here: a provider that was just switched off,
    emptied or replaced must never be the only remaining way in.
    """
    db.flush()
    if not oidc_login_available(load_config(db)) and not password_login_enabled(db):
        save_setting(db, PASSWORD_LOGIN_ENABLED_SETTING, "true")


def _revoke_all_sessions_during_recovery(db: Session) -> None:
    """Drop every session when a change was made through emergency access."""
    if auth_disabled_by_environment():
        db.query(UserSession).delete()


def _record_check(db: Session, status: str, *, issuer: str = "", error: str = "") -> None:
    save_setting(db, OIDC_CHECK_STATUS_SETTING, status)
    save_setting(db, OIDC_CHECK_AT_SETTING, utc_now().replace(microsecond=0).isoformat())
    save_setting(db, OIDC_CHECK_ERROR_SETTING, error)
    if issuer:
        save_setting(db, OIDC_ISSUER_SETTING, issuer)


@router.post("/settings/auth/oidc")
async def save_oidc_configuration(
    request: Request,
    discovery_url: str = Form(""),
    client_id: str = Form(""),
    client_secret: str = Form(""),
    db: Session = Depends(get_db),
):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    # Changing the provider while it is the only sign-in method would lock every
    # administrator out; emergency access is the one place it has to stay possible.
    if not effective_password_login_enabled(db) and not auth_disabled_by_environment():
        return _error("password_login_locked")

    config = load_config(db)
    normalized_url = discovery_url.strip()
    normalized_client_id = client_id.strip()
    # An empty secret field keeps the stored secret: the current value is never
    # rendered back into the form, so an admin editing only the client ID must
    # not have to re-enter it.
    effective_secret = client_secret if client_secret else config.client_secret
    if not normalized_url or not normalized_client_id or not effective_secret:
        return _error("incomplete_config")

    try:
        issuer, _metadata = await check_provider(normalized_url)
    except OidcConfigurationError as exc:
        # The previous configuration stays untouched: a broken new provider must
        # never replace a working one.
        logger.warning("OIDC provider check failed code=%s", exc.code)
        return _error(exc.code)

    provider_changed = (
        normalized_url != config.discovery_url
        or normalized_client_id != config.client_id
        or effective_secret != config.client_secret
        or issuer != config.issuer
    )
    save_setting(db, OIDC_DISCOVERY_URL_SETTING, normalized_url)
    save_setting(db, OIDC_CLIENT_ID_SETTING, normalized_client_id)
    save_setting(db, OIDC_CLIENT_SECRET_SETTING, effective_secret)
    _record_check(db, CHECK_STATUS_HEALTHY, issuer=issuer)
    if provider_changed:
        # Sessions were created against the old provider, and existing identity
        # rows are never silently rewritten onto the new one.
        delete_sessions_by_auth_method(db, AUTH_METHOD_OIDC)
    _revoke_all_sessions_during_recovery(db)
    _enforce_login_methods(db)
    db.commit()
    invalidate_provider_cache()
    return _notice("configuration_saved")


@router.post("/settings/auth/oidc/enable")
async def enable_oidc(request: Request, db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error

    config = load_config(db)
    if not config.complete:
        return _error("incomplete_config")
    try:
        issuer, _metadata = await check_provider(config.discovery_url)
    except OidcConfigurationError as exc:
        logger.warning("OIDC provider check failed code=%s", exc.code)
        _record_check(db, CHECK_STATUS_ERROR, error=exc.code)
        db.commit()
        return _error(exc.code)
    if config.issuer and issuer != config.issuer:
        _record_check(db, CHECK_STATUS_ERROR, error="invalid_issuer")
        db.commit()
        return _error("invalid_issuer")

    _record_check(db, CHECK_STATUS_HEALTHY, issuer=issuer)
    save_setting(db, OIDC_ENABLED_SETTING, "true")
    _enforce_login_methods(db)
    db.commit()
    invalidate_provider_cache()
    return _notice("enabled")


@router.post("/settings/auth/oidc/disable")
def disable_oidc(request: Request, db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    save_setting(db, OIDC_ENABLED_SETTING, "false")
    delete_sessions_by_auth_method(db, AUTH_METHOD_OIDC)
    # Password sign-in comes back in the same transaction: the provider was just
    # taken away, so it is the only method left.
    _enforce_login_methods(db)
    _revoke_all_sessions_during_recovery(db)
    db.commit()
    invalidate_provider_cache()
    return _notice("disabled")


@router.post("/settings/auth/oidc/secret/delete")
def delete_oidc_client_secret(request: Request, db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    if not effective_password_login_enabled(db) and not auth_disabled_by_environment():
        return _error("password_login_locked")
    save_setting(db, OIDC_CLIENT_SECRET_SETTING, "")
    save_setting(db, OIDC_ENABLED_SETTING, "false")
    _record_check(db, CHECK_STATUS_ERROR, error="incomplete_config")
    delete_sessions_by_auth_method(db, AUTH_METHOD_OIDC)
    _enforce_login_methods(db)
    _revoke_all_sessions_during_recovery(db)
    db.commit()
    invalidate_provider_cache()
    return _notice("secret_deleted")


@router.post("/settings/auth/oidc/jit")
def save_oidc_jit(request: Request, jit_enabled: str = Form(""), db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    # Switching this only decides what happens to future unknown identities: no
    # existing user changes and no session is revoked.
    save_setting(db, OIDC_JIT_ENABLED_SETTING, "true" if jit_enabled == "true" else "false")
    _enforce_login_methods(db)
    db.commit()
    invalidate_provider_cache()
    return _notice("jit_saved")


@router.post("/settings/auth/password-login/disable")
def disable_password_login(request: Request, db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    if not auth_enabled(db):
        return _error("password_login_needs_auth")
    config = load_config(db)
    if not oidc_login_available(config):
        return _error("password_login_needs_oidc")

    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin" or not user.is_active:
        return _error("password_login_needs_admin")
    # The proof that single sign-on really works for this administrator is that
    # the session they are acting in was created by it. A stored discovery
    # answer alone would still allow locking everybody out.
    if getattr(request.state, "auth_method", None) != AUTH_METHOD_OIDC:
        return _error("password_login_needs_oidc_session")
    identity = find_user_external_identity(db, user.id)
    if identity is None or identity.issuer != config.issuer:
        return _error("password_login_needs_link")
    if active_oidc_admin_count(db, config.issuer) == 0:
        return _error("password_login_needs_oidc_admin")

    save_setting(db, PASSWORD_LOGIN_ENABLED_SETTING, "false")
    delete_sessions_by_auth_method(db, AUTH_METHOD_PASSWORD)
    db.commit()
    return _notice("password_login_disabled")


@router.post("/settings/auth/password-login/enable")
def enable_password_login(request: Request, db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    # Turning a sign-in method back on is always allowed and revokes nothing,
    # so a locked-out administrator can recover without losing provider sessions.
    save_setting(db, PASSWORD_LOGIN_ENABLED_SETTING, "true")
    _revoke_all_sessions_during_recovery(db)
    db.commit()
    return _notice("password_login_enabled")
