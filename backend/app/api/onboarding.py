"""The standalone first-admin onboarding page and its completion."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.i18n import is_available_language, language_options, resolve_language, translate
from app.core.template_context import get_setting_value
from app.core.version import get_app_version
from app.database.dependencies import get_db
from app.services.auth import (
    AUTH_ONBOARDING_COMPLETE,
    auth_disabled_by_environment,
    auth_enabled,
    normalize_auth_hostname,
    onboarding_state,
)
from app.services.onboarding import ERROR_ALREADY_COMPLETED, account_required, complete_onboarding
from app.web.auth import auth_proxy_error, auth_transport_diagnostics
from app.web.templates import templates

router = APIRouter(tags=["onboarding"])

ONBOARDING_PATH = "/onboarding"

# Codes whose meaning is identical to the existing activation in settings reuse
# its messages; the remaining ones are specific to the setup page.
_SHARED_ERROR_CODES = {
    "invalid_hostname",
    "proxy_not_configured",
    "https_required",
    "https_port_required",
    "hostname_mismatch",
    "password_mismatch",
    "invalid_username",
    "username_taken",
    "password_too_short",
}


def _error_key(error: str) -> str:
    return f"settings.auth_error.{error}" if error in _SHARED_ERROR_CODES else f"onboarding.error.{error}"


def _selected_language(db: Session, requested: str) -> str:
    """Return the language to render in: request, then global setting, then English."""
    if is_available_language(requested):
        return requested
    return resolve_language(get_setting_value(db, "language", ""))


def _onboarding_page(
    request: Request,
    db: Session,
    state: str,
    *,
    language: str,
    hostname: str = "",
    error: str = "",
    status_code: int = 200,
):
    """Render the standalone setup page without any instance or account data."""
    return templates.TemplateResponse(
        request=request,
        name="onboarding.html",
        status_code=status_code,
        context={
            "language": language,
            "t": lambda key: translate(key, language),
            "language_options": language_options(),
            # While the break-glass variable is set the page only explains the
            # situation: nothing here may complete or change the stored state.
            "break_glass": auth_disabled_by_environment(),
            # Only whether an account has to be created is shown, never how many
            # accounts exist, their names, roles or any other detail.
            "account_required": account_required(db, state),
            "hostname": hostname,
            "auth_transport": auth_transport_diagnostics(request, hostname),
            "error": error,
            "error_key": _error_key(error) if error else "",
            "app_version": get_app_version(),
        },
    )


def _completed_redirect(db: Session) -> RedirectResponse:
    return RedirectResponse("/login" if auth_enabled(db) else "/", status_code=303)


@router.get(ONBOARDING_PATH)
def onboarding_page(request: Request, language: str = "", db: Session = Depends(get_db)):
    state = onboarding_state(db)
    if state == AUTH_ONBOARDING_COMPLETE:
        return _completed_redirect(db)
    return _onboarding_page(request, db, state, language=_selected_language(db, language))


@router.post(ONBOARDING_PATH)
def complete_onboarding_form(
    request: Request,
    language: str = Form(""),
    hostname: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    state = onboarding_state(db)
    if state == AUTH_ONBOARDING_COMPLETE:
        return _completed_redirect(db)
    selected_language = _selected_language(db, language)

    def rejected(error: str):
        return _onboarding_page(
            request,
            db,
            state,
            language=selected_language,
            hostname=hostname,
            error=error,
            status_code=400,
        )

    if auth_disabled_by_environment():
        # Break-glass keeps the installation open on purpose. Completing the
        # setup from here would silently take that decision away again, so the
        # stored state stays untouched until the variable is removed.
        return rejected("break_glass_active")
    if not is_available_language(language):
        return rejected("invalid_language")
    normalized_hostname = normalize_auth_hostname(hostname)
    if normalized_hostname is None:
        return rejected("invalid_hostname")
    proxy_error = auth_proxy_error(request, normalized_hostname)
    if proxy_error is not None:
        return rejected(proxy_error)
    if account_required(db, state):
        if password != password_confirm:
            return rejected("password_mismatch")
        error = complete_onboarding(
            db,
            language=selected_language,
            hostname=normalized_hostname,
            username=username,
            password=password,
        )
    else:
        if username or password or password_confirm:
            # An upgraded installation that still has an admin only confirms its
            # hostname here; submitted account data is refused instead of
            # silently dropped, so the form never looks like an account change.
            return rejected("account_not_allowed")
        error = complete_onboarding(db, language=selected_language, hostname=normalized_hostname)
    if error == ERROR_ALREADY_COMPLETED:
        # A second first visitor lost the race for the same claim. Nothing was
        # written for them, and the finished installation is simply shown.
        return _completed_redirect(db)
    if error is not None:
        return rejected(error)
    # Deliberately no session and no cookie: the first administrator proves the
    # normal login path works before reaching the application.
    return RedirectResponse("/login", status_code=303)
