"""Settings routes for internal authentication and user management."""
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.users import User, UserPreference, UserSession
from app.services.auth import (
    AUTH_DISABLED_ENV,
    PASSWORD_MIN_LENGTH,
    ROLES,
    active_admin_count,
    create_session,
    create_user,
    delete_user_sessions,
    hash_password,
    validate_new_user,
)
from app.web.auth import set_session_cookie
from app.web.tables import save_setting

router = APIRouter(tags=["users"])


def _settings_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?auth_error={code}", status_code=303)


def _auth_disabled_by_environment() -> bool:
    return os.environ.get(AUTH_DISABLED_ENV, "").lower() in ("1", "true", "yes")


def _user_or_error(db: Session, user_id: int) -> User | RedirectResponse:
    user = db.query(User).filter(User.id == user_id).first()
    return user if user is not None else _settings_error("unknown_user")


@router.post("/settings/auth/enable")
def enable_authentication(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    db: Session = Depends(get_db),
):
    if _auth_disabled_by_environment():
        return _settings_error("env_disabled")
    if username or password or password_confirm:
        if password != password_confirm:
            return _settings_error("password_mismatch")
        error = validate_new_user(db, username, password)
        if error is not None:
            return _settings_error(error)
        user = create_user(db, username, password, "admin")
        save_setting(db, "auth.enabled", "true")
        token = create_session(db, user)
        db.commit()
        response = RedirectResponse("/settings", status_code=303)
        set_session_cookie(response, request, token)
        return response
    if active_admin_count(db) == 0:
        return _settings_error("no_admin")
    save_setting(db, "auth.enabled", "true")
    db.commit()
    return RedirectResponse("/login", status_code=303)


@router.post("/settings/auth/disable")
def disable_authentication(db: Session = Depends(get_db)):
    save_setting(db, "auth.enabled", "false")
    db.query(UserSession).delete()
    db.commit()
    return RedirectResponse("/settings", status_code=303)


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
    if user.role == "admin" and user.is_active and role != "admin" and active_admin_count(db, exclude_user_id=user.id) == 0:
        return _settings_error("last_admin")
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
    if user.is_active and user.role == "admin" and active_admin_count(db, exclude_user_id=user.id) == 0:
        return _settings_error("last_admin")
    user.is_active = not user.is_active
    if not user.is_active:
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
    if user.is_active and user.role == "admin" and active_admin_count(db, exclude_user_id=user.id) == 0:
        return _settings_error("last_admin")
    delete_user_sessions(db, user.id)
    db.query(UserPreference).filter(UserPreference.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return RedirectResponse("/settings", status_code=303)
