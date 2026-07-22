"""Login and logout routes for optional internal authentication."""
import time
import hashlib
from collections import OrderedDict
from threading import Lock

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.i18n import translate
from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.core.version import get_app_version
from app.database.dependencies import get_db
from app.models.users import User, UserPreference
from app.services.auth import (
    AUTH_METHOD_PASSWORD,
    PASSWORD_MIN_LENGTH,
    auth_enabled,
    authenticate,
    cleanup_expired_sessions,
    create_session,
    delete_session,
    delete_user_sessions,
    hash_password,
    normalize_username,
    resolve_session,
    verify_password,
)
from app.services.user_preferences import normalize_preferences
from app.web.auth import SESSION_COOKIE, set_session_cookie
from app.web.proxy_headers import PROXY_STATE_PEER_ADDRESS
from app.web.redirects import safe_local_path
from app.web.render import render
from app.web.templates import templates

router = APIRouter(tags=["auth"])

_LOGIN_BACKOFF: OrderedDict[str, tuple[int, float, float]] = OrderedDict()
_LOGIN_BACKOFF_LOCK = Lock()
# Account throttling stops distributed guessing; the higher peer threshold is a
# backstop for password spraying without penalizing a shared homelab proxy quickly.
_MAX_ACCOUNT_LOGIN_FAILURES = 5
_MAX_SOURCE_LOGIN_FAILURES = 100
_LOGIN_LOCK_SECONDS = 60
_LOGIN_BACKOFF_TTL_SECONDS = 300
_MAX_LOGIN_BACKOFF_ENTRIES = 4096


def _login_backoff_key(bucket: str, identity: str) -> str:
    return hashlib.sha256(f"{bucket}\0{identity}".encode("utf-8")).hexdigest()


def _account_backoff_key(username: str) -> str:
    return _login_backoff_key("account", normalize_username(username))


def _source_id(request: Request) -> str:
    peer_address = getattr(request.state, PROXY_STATE_PEER_ADDRESS, None)
    if isinstance(peer_address, str) and peer_address:
        return peer_address
    return request.client.host if request.client is not None else "unknown"


def _source_backoff_key(source_id: str) -> str:
    return _login_backoff_key("source", source_id)


def reset_login_backoff() -> None:
    """Clear the in-memory login backoff state for tests."""
    with _LOGIN_BACKOFF_LOCK:
        _LOGIN_BACKOFF.clear()


def _login_locked(username: str, source_id: str) -> bool:
    keys = (_account_backoff_key(username), _source_backoff_key(source_id))
    with _LOGIN_BACKOFF_LOCK:
        now = time.monotonic()
        return any((attempt := _LOGIN_BACKOFF.get(key)) is not None and attempt[1] > now for key in keys)


def _record_failed_login(username: str, source_id: str) -> None:
    now = time.monotonic()
    with _LOGIN_BACKOFF_LOCK:
        while _LOGIN_BACKOFF:
            _oldest_key, (_failures, _locked_until, last_seen) = next(iter(_LOGIN_BACKOFF.items()))
            if last_seen > now - _LOGIN_BACKOFF_TTL_SECONDS:
                break
            _LOGIN_BACKOFF.popitem(last=False)
        for key, threshold in (
            (_account_backoff_key(username), _MAX_ACCOUNT_LOGIN_FAILURES),
            (_source_backoff_key(source_id), _MAX_SOURCE_LOGIN_FAILURES),
        ):
            failures, locked_until, _last_seen = _LOGIN_BACKOFF.get(key, (0, 0.0, 0.0))
            if locked_until and locked_until <= now:
                failures = 0
            failures += 1
            _LOGIN_BACKOFF[key] = (
                failures,
                now + _LOGIN_LOCK_SECONDS if failures >= threshold else 0.0,
                now,
            )
            _LOGIN_BACKOFF.move_to_end(key)
        while len(_LOGIN_BACKOFF) > _MAX_LOGIN_BACKOFF_ENTRIES:
            _LOGIN_BACKOFF.popitem(last=False)


def _login_response(request: Request, db: Session, next_path: str, *, error: bool = False, error_key: str = "auth.login_failed", status_code: int = 200):
    language = get_setting_value(db, "language", "en")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        status_code=status_code,
        context={
            "language": language,
            "t": lambda key: translate(key, language),
            "next": safe_local_path(next_path),
            "error": error,
            "error_key": error_key,
            "app_version": get_app_version(),
        },
    )


@router.get("/login")
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if not auth_enabled(db) or resolve_session(db, request.cookies.get(SESSION_COOKIE, "")) is not None:
        return RedirectResponse("/", status_code=303)
    return _login_response(request, db, next)


@router.post("/login")
def login(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    if not auth_enabled(db):
        return RedirectResponse("/", status_code=303)
    source_id = _source_id(request)
    if _login_locked(username, source_id):
        return _login_response(request, db, next, error=True, error_key="auth.login_locked", status_code=429)

    user = authenticate(db, username, password)
    if user is None:
        _record_failed_login(username, source_id)
        return _login_response(request, db, next, error=True, status_code=401)

    with _LOGIN_BACKOFF_LOCK:
        _LOGIN_BACKOFF.pop(_account_backoff_key(username), None)
    cleanup_expired_sessions(db)
    user.last_login_at = utc_now().replace(tzinfo=None)
    token = create_session(db, user, AUTH_METHOD_PASSWORD)
    db.commit()
    response = RedirectResponse(safe_local_path(next), status_code=303)
    set_session_cookie(response, request, token)
    return response


@router.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        delete_session(db, token)
        db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/account")
def account_page(request: Request, db: Session = Depends(get_db)):
    if not auth_enabled(db):
        return RedirectResponse("/", status_code=303)
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return render(
        request,
        db,
        "account.html",
        account_user=user,
        auth_error=request.query_params.get("auth_error", ""),
        auth_notice=request.query_params.get("auth_notice", ""),
    )


@router.post("/account/preferences")
def change_preferences(
    request: Request,
    language: str = Form(),
    live_default: str = Form(),
    theme: str = Form(),
    accent_color: str = Form(),
    live_page_refresh: str = Form(),
    db: Session = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    submitted_preferences = {
        "language": language,
        "live_default": live_default,
        "theme": theme,
        "accent_color": accent_color,
        "live_page_refresh": live_page_refresh,
    }
    preferences = normalize_preferences(submitted_preferences)
    if preferences != submitted_preferences:
        return RedirectResponse("/account?auth_error=invalid_preferences", status_code=303)
    user_preferences = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
    if user_preferences is None:
        return RedirectResponse("/account?auth_error=invalid_preferences", status_code=303)
    user_preferences.language = preferences["language"]
    user_preferences.live_default = preferences["live_default"]
    user_preferences.theme = preferences["theme"]
    user_preferences.accent_color = preferences["accent_color"]
    user_preferences.live_page_refresh = preferences["live_page_refresh"]
    db.commit()
    return RedirectResponse("/account?auth_notice=preferences_saved", status_code=303)


@router.post("/auth/password")
def change_password(
    request: Request,
    current_password: str = Form(),
    new_password: str = Form(),
    new_password_confirm: str = Form(),
    db: Session = Depends(get_db),
):
    session_user = getattr(request.state, "user", None)
    if session_user is None:
        return RedirectResponse("/login", status_code=303)
    user = db.query(User).filter(User.id == session_user.id).first()
    if user is None or not verify_password(current_password, user.password_hash):
        return RedirectResponse("/account?auth_error=wrong_password", status_code=303)
    if len(new_password) < PASSWORD_MIN_LENGTH:
        return RedirectResponse("/account?auth_error=password_too_short", status_code=303)
    if new_password != new_password_confirm:
        return RedirectResponse("/account?auth_error=password_mismatch", status_code=303)

    user.password_hash = hash_password(new_password)
    delete_user_sessions(db, user.id)
    token = create_session(db, user, AUTH_METHOD_PASSWORD)
    db.commit()
    response = RedirectResponse("/account?auth_notice=password_changed", status_code=303)
    set_session_cookie(response, request, token)
    return response
