"""Admin routes for the single generic OIDC provider configuration."""
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.database.dependencies import get_db
from app.services.auth import (
    AUTH_HOSTNAME_SETTING,
    AUTH_METHOD_OIDC,
    auth_disabled_by_environment,
    delete_sessions_by_auth_method,
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
    OidcConfigurationError,
    check_provider,
    invalidate_provider_cache,
    load_config,
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
    if not password_login_enabled(db):
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

    save_setting(db, OIDC_DISCOVERY_URL_SETTING, normalized_url)
    save_setting(db, OIDC_CLIENT_ID_SETTING, normalized_client_id)
    save_setting(db, OIDC_CLIENT_SECRET_SETTING, effective_secret)
    _record_check(db, CHECK_STATUS_HEALTHY, issuer=issuer)
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
    db.commit()
    invalidate_provider_cache()
    return _notice("disabled")


@router.post("/settings/auth/oidc/secret/delete")
def delete_oidc_client_secret(request: Request, db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    if not password_login_enabled(db):
        return _error("password_login_locked")
    save_setting(db, OIDC_CLIENT_SECRET_SETTING, "")
    save_setting(db, OIDC_ENABLED_SETTING, "false")
    _record_check(db, CHECK_STATUS_ERROR, error="incomplete_config")
    delete_sessions_by_auth_method(db, AUTH_METHOD_OIDC)
    db.commit()
    invalidate_provider_cache()
    return _notice("secret_deleted")


@router.post("/settings/auth/oidc/jit")
def save_oidc_jit(request: Request, jit_enabled: str = Form(""), db: Session = Depends(get_db)):
    boundary_error = _boundary_error(request, db)
    if boundary_error is not None:
        return boundary_error
    save_setting(db, OIDC_JIT_ENABLED_SETTING, "true" if jit_enabled == "true" else "false")
    db.commit()
    invalidate_provider_cache()
    return _notice("jit_saved")
