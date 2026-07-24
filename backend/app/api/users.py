"""Settings routes for internal authentication and user management."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.users import User, UserPreference, UserSession
from app.services.auth import (
    AUTH_HOSTNAME_SETTING,
    PASSWORD_MIN_LENGTH,
    ROLES,
    auth_disabled_by_environment,
    create_user,
    delete_user_external_identities,
    delete_user_sessions,
    hash_password,
    normalize_auth_hostname,
    onboarding_state,
    unlink_external_identity,
    validate_new_user,
)
from app.services.oidc import admin_reachability_error
from app.services.onboarding import account_required, complete_onboarding
from app.web.auth import auth_proxy_error
from app.web.tables import save_setting

router = APIRouter(tags=["users"])


def _settings_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?auth_error={code}", status_code=303)


def _user_or_error(db: Session, user_id: int) -> User | RedirectResponse:
    user = db.query(User).filter(User.id == user_id).first()
    return user if user is not None else _settings_error("unknown_user")


@router.post("/settings/auth/enable")
def enable_authentication(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    hostname: str = Form(""),
    db: Session = Depends(get_db),
):
    """Activate internal sign-in on an installation that is still open.

    This is the settings entrance to the same guided setup as ``/onboarding``.
    It writes through the same claiming service, so both paths create at most
    one first administrator and neither of them signs anybody in.
    """
    if auth_disabled_by_environment():
        return _settings_error("env_disabled")
    normalized_hostname = normalize_auth_hostname(hostname)
    if normalized_hostname is None:
        return _settings_error("invalid_hostname")
    proxy_error = auth_proxy_error(request, normalized_hostname)
    if proxy_error is not None:
        return _settings_error(proxy_error)
    if account_required(db, onboarding_state(db)):
        if password != password_confirm:
            return _settings_error("password_mismatch")
    elif username or password or password_confirm:
        # With an active admin this form only confirms the hostname; submitted
        # account data is refused instead of silently dropped, so the form never
        # looks like an account change.
        return _settings_error("account_not_allowed")
    error = complete_onboarding(db, hostname=normalized_hostname, username=username, password=password)
    if error is not None:
        return _settings_error(error)
    # Deliberately no session and no cookie: the activation ends at the normal
    # login, exactly like the setup page.
    return RedirectResponse("/login", status_code=303)


@router.post("/settings/auth/hostname")
def repair_authentication_hostname(hostname: str = Form(""), db: Session = Depends(get_db)):
    if not auth_disabled_by_environment():
        return _settings_error("recovery_only")
    normalized_hostname = normalize_auth_hostname(hostname)
    if normalized_hostname is None:
        return _settings_error("invalid_hostname")
    save_setting(db, AUTH_HOSTNAME_SETTING, normalized_hostname)
    db.query(UserSession).delete()
    db.commit()
    return RedirectResponse("/settings?auth_notice=hostname_saved", status_code=303)


@router.post("/settings/users/create")
def create_managed_user(
    username: str = Form(),
    password: str = Form(),
    role: str = Form("viewer"),
    db: Session = Depends(get_db),
):
    if role not in ROLES:
        return _settings_error("invalid_role")
    error = validate_new_user(db, username, password)
    if error is not None:
        return _settings_error(error)
    create_user(db, username, password, role)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/users/{user_id}/role")
def change_user_role(user_id: int, role: str = Form(), db: Session = Depends(get_db)):
    if role not in ROLES:
        return _settings_error("invalid_role")
    user = _user_or_error(db, user_id)
    if isinstance(user, RedirectResponse):
        return user
    error = admin_reachability_error(db, user, role=role)
    if error is not None:
        return _settings_error(error)
    user.role = role
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/users/{user_id}/password")
def reset_user_password(user_id: int, password: str = Form(), db: Session = Depends(get_db)):
    if len(password) < PASSWORD_MIN_LENGTH:
        return _settings_error("password_too_short")
    user = _user_or_error(db, user_id)
    if isinstance(user, RedirectResponse):
        return user
    user.password_hash = hash_password(password)
    delete_user_sessions(db, user.id)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/users/password")
def reset_selected_user_password(user_id: int = Form(), password: str = Form(), db: Session = Depends(get_db)):
    return reset_user_password(user_id, password, db)


@router.post("/settings/users/{user_id}/toggle")
def toggle_user(user_id: int, db: Session = Depends(get_db)):
    user = _user_or_error(db, user_id)
    if isinstance(user, RedirectResponse):
        return user
    error = admin_reachability_error(db, user, is_active=not user.is_active)
    if error is not None:
        return _settings_error(error)
    user.is_active = not user.is_active
    if not user.is_active:
        delete_user_sessions(db, user.id)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/users/{user_id}/oidc/unlink")
def revoke_user_external_identity(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = _user_or_error(db, user_id)
    if isinstance(user, RedirectResponse):
        return user
    current_user = getattr(request.state, "user", None)
    if current_user is not None and current_user.id == user.id:
        # Removing your own link has stricter conditions and lives on the
        # account page, so it cannot be used to walk around them here.
        return _settings_error("self_unlink")
    error = admin_reachability_error(db, user, keeps_identity=False)
    if error is not None:
        return _settings_error(error)
    if not unlink_external_identity(db, user.id):
        return _settings_error("not_linked")
    # Revoking a link is a deliberate withdrawal of access, so the sessions it
    # created end with it.
    delete_user_sessions(db, user.id)
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/users/{user_id}/delete")
def delete_managed_user(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = _user_or_error(db, user_id)
    if isinstance(user, RedirectResponse):
        return user
    current_user = getattr(request.state, "user", None)
    if current_user is not None and current_user.id == user.id:
        return _settings_error("self_delete")
    error = admin_reachability_error(db, user, deleted=True)
    if error is not None:
        return _settings_error(error)
    delete_user_sessions(db, user.id)
    delete_user_external_identities(db, user.id)
    db.query(UserPreference).filter(UserPreference.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return RedirectResponse("/settings", status_code=303)
