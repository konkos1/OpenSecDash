"""Break-glass as the only opt-out and the permanent upgrade review (plan phase 3)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.api.pages import build_debug_report
from app.core.i18n import language_options
from app.core.template_context import get_setting_value
from app.database.base import Base
from app.database.dependencies import get_db
from app.locales import LOCALES
from app.main import app
from app.models.settings import Setting
from app.models.users import User, UserSession
from app.services.auth import (
    AUTH_ENABLED_SETTING,
    AUTH_HOSTNAME_SETTING,
    AUTH_ONBOARDING_COMPLETE,
    AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED,
    AUTH_ONBOARDING_PENDING,
    AUTH_ONBOARDING_STATE_SETTING,
    AUTH_ONBOARDING_STATES,
    create_user,
)

REVIEW_BANNER = "auth-review-prompt"
BREAK_GLASS_BANNER = "auth-disabled-warning"
ACTIVATION = {
    "hostname": "testserver",
    "username": "admin",
    "password": "password123",
    "password_confirm": "password123",
}


@pytest.fixture()
def review_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session = session_factory()

    def get_test_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr("app.web.auth.SessionLocal", session_factory)
    monkeypatch.setattr("app.main.SessionLocal", session_factory)
    monkeypatch.setattr("app.main.init_db", lambda: None)
    app.dependency_overrides[get_db] = get_test_db
    auth_api.reset_login_backoff()
    client = TestClient(app, base_url="https://testserver")
    try:
        yield db_session, client
    finally:
        client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        db_session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def set_state(db_session, state, *, auth_enabled, hostname=""):
    """Seed the settings a started installation carries in the given state."""
    settings = [
        Setting(key=AUTH_ENABLED_SETTING, value=auth_enabled),
        Setting(key=AUTH_ONBOARDING_STATE_SETTING, value=state),
        Setting(key="plugin.traefik_log.enabled", value="true"),
    ]
    if hostname:
        settings.append(Setting(key=AUTH_HOSTNAME_SETTING, value=hostname))
    db_session.add_all(settings)
    db_session.commit()


def value(db_session, key):
    db_session.expire_all()
    setting = db_session.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting is not None else None


def language_select(page_text):
    return page_text.split('name="language"', maxsplit=1)[1].split("</select>", maxsplit=1)[0]


def test_the_old_disable_endpoint_is_gone_and_changes_nothing(review_client):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_COMPLETE, auth_enabled="true", hostname="testserver")
    create_user(db_session, "admin", "password123", "admin")
    db_session.commit()
    client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)
    assert db_session.query(UserSession).count() == 1

    response = client.post("/settings/auth/disable", follow_redirects=False)

    assert response.status_code == 404
    assert value(db_session, AUTH_ENABLED_SETTING) == "true"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE
    assert db_session.query(UserSession).count() == 1


def test_settings_offers_no_persistent_deactivation_while_sign_in_is_on(review_client):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_COMPLETE, auth_enabled="true", hostname="testserver")
    create_user(db_session, "admin", "password123", "admin")
    db_session.commit()
    client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)

    page = client.get("/settings")

    assert page.status_code == 200
    assert "/settings/auth/disable" not in page.text
    assert "Disable internal sign-in" not in page.text
    assert "OSD_AUTH_DISABLED=true" in page.text
    assert REVIEW_BANNER not in page.text


def test_an_open_upgraded_installation_stays_usable_and_shows_the_review_prompt(review_client):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert client.get("/api/events").status_code == 200
    assert client.get("/access").status_code == 200
    assert client.get("/settings").status_code == 200
    with client.websocket_connect("wss://testserver/ws/events") as websocket:
        assert websocket.receive_json()["type"] == "connected"

    # Permanent and without any way out: no close, later or dismiss control.
    assert REVIEW_BANNER in dashboard.text
    # Exactly one global banner: without the override the review prompt stands
    # alone and the break-glass warning does not appear alongside it.
    assert BREAK_GLASS_BANNER not in dashboard.text
    assert "Decide how this installation is protected." in dashboard.text
    assert 'href="/onboarding"' in dashboard.text
    assert "OSD_AUTH_DISABLED=true" in dashboard.text
    assert "Until you do one of the two, this notice stays." in dashboard.text
    prompt = dashboard.text.split(REVIEW_BANNER, maxsplit=1)[1].split("</aside>", maxsplit=1)[0]
    for dismissal in ("<button", "dismiss", "hidden", "later"):
        assert dismissal not in prompt.lower(), dismissal


def test_the_review_prompt_appears_on_every_full_page(review_client):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")

    for path in ("/", "/diagnostics", "/notifications", "/settings", "/access"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert REVIEW_BANNER in page.text, path

    # A partial htmx response must not duplicate the banner into a loaded page.
    fragment = client.get("/fragments/backlog-banner")
    assert fragment.status_code == 200
    assert REVIEW_BANNER not in fragment.text


def test_activation_completes_the_review_and_leaves_no_way_back_in_the_ui(review_client):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")
    assert REVIEW_BANNER in client.get("/settings").text

    response = client.post("/settings/auth/enable", data=ACTIVATION, follow_redirects=False)

    # Like the setup page the activation ends at the login, without a session.
    assert response.headers["location"] == "/login"
    assert db_session.query(UserSession).count() == 0
    assert value(db_session, AUTH_ENABLED_SETTING) == "true"
    assert value(db_session, AUTH_HOSTNAME_SETTING) == "testserver"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE

    client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)
    page = client.get("/settings")
    assert page.status_code == 200
    assert REVIEW_BANNER not in page.text
    assert "/settings/auth/disable" not in page.text


def test_break_glass_opens_a_finished_installation_without_rewriting_it(review_client, monkeypatch):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_COMPLETE, auth_enabled="true", hostname="testserver")
    create_user(db_session, "admin", "password123", "admin")
    db_session.commit()
    assert client.get("/", follow_redirects=False).status_code == 303

    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")
    opened = client.get("/")

    assert opened.status_code == 200
    assert BREAK_GLASS_BANNER in opened.text
    assert value(db_session, AUTH_ENABLED_SETTING) == "true"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE

    monkeypatch.delenv("OSD_AUTH_DISABLED")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_break_glass_during_a_pending_setup_neither_redirects_nor_completes(review_client, monkeypatch):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_PENDING, auth_enabled="true")
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert BREAK_GLASS_BANNER in dashboard.text
    assert REVIEW_BANNER not in dashboard.text

    settings = client.get("/settings")
    assert "The first-time setup of this installation is not finished." in settings.text

    page = client.get("/onboarding")
    assert page.status_code == 200
    assert "OSD_AUTH_DISABLED is set" in page.text
    assert 'method="post"' not in page.text

    blocked = client.post("/onboarding", data={**ACTIVATION, "language": "en"}, follow_redirects=False)
    assert blocked.status_code == 400
    assert db_session.query(User).count() == 0
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING
    assert value(db_session, AUTH_HOSTNAME_SETTING) is None

    # Removing the variable brings the setup mode back unchanged.
    monkeypatch.delenv("OSD_AUTH_DISABLED")
    redirected = client.get("/", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == "/onboarding"


def test_break_glass_replaces_the_review_prompt_and_restores_it_afterwards(review_client, monkeypatch):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")

    opened = client.get("/")
    assert BREAK_GLASS_BANNER in opened.text
    assert REVIEW_BANNER not in opened.text
    assert value(db_session, AUTH_ENABLED_SETTING) == "false"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED

    monkeypatch.delenv("OSD_AUTH_DISABLED")
    restored = client.get("/")
    assert REVIEW_BANNER in restored.text


def test_break_glass_warning_stays_on_every_page_and_cannot_be_dismissed(review_client, monkeypatch):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_COMPLETE, auth_enabled="true", hostname="testserver")
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")

    for path in ("/", "/diagnostics", "/notifications", "/settings", "/access"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert BREAK_GLASS_BANNER in page.text, path
        warning = page.text.split(BREAK_GLASS_BANNER, maxsplit=1)[1].split("</aside>", maxsplit=1)[0]
        assert "<button" not in warning.lower(), path
        # The core statements the plan requires on every page, and no advice to
        # enable sign-in, which the override blocks.
        assert "OSD_AUTH_DISABLED is set" in warning, path
        assert "internal roles and the authentication hostname boundary no longer apply" in warning, path
        assert "external authentication proxy" in warning, path
        assert "restart to restore the saved sign-in state" in warning, path
        assert "enable internal sign-in" not in warning.lower(), path

    assert "the saved authentication state applies again" in client.get("/settings").text


def test_recovery_actions_still_repair_hostname_password_login_and_oidc(review_client, monkeypatch):
    db_session, client = review_client
    set_state(db_session, AUTH_ONBOARDING_COMPLETE, auth_enabled="true", hostname="old.example")
    db_session.add_all(
        [
            Setting(key="auth.password_login_enabled", value="false"),
            Setting(key="auth.oidc.enabled", value="true"),
        ]
    )
    create_user(db_session, "admin", "password123", "admin")
    db_session.commit()
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")

    hostname = client.post("/settings/auth/hostname", data={"hostname": "new.example"}, follow_redirects=False)
    password_login = client.post("/settings/auth/password-login/enable", follow_redirects=False)
    oidc = client.post("/settings/auth/oidc/disable", follow_redirects=False)

    assert hostname.headers["location"] == "/settings?auth_notice=hostname_saved"
    assert password_login.status_code == 303
    assert oidc.status_code == 303
    assert value(db_session, AUTH_HOSTNAME_SETTING) == "new.example"
    db_session.expire_all()
    assert get_setting_value(db_session, "auth.password_login_enabled", "") == "true"
    assert get_setting_value(db_session, "auth.oidc.enabled", "") == "false"
    # The stored global switch and the setup state stay exactly as they were.
    assert value(db_session, AUTH_ENABLED_SETTING) == "true"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE


@pytest.mark.parametrize("state", AUTH_ONBOARDING_STATES)
def test_diagnostics_and_debug_report_explain_the_state_without_details(review_client, state):
    db_session, client = review_client
    set_state(db_session, state, auth_enabled="false", hostname="dash.example.com")
    report = build_debug_report(db_session)

    assert f"Onboarding state: {state}" in report
    assert "Stored authentication: false" in report
    assert "Effective onboarding" in report
    assert "dash.example.com" not in report.split("auth.hostname", maxsplit=1)[0]
    if state == AUTH_ONBOARDING_PENDING:
        # A pending setup shows no diagnostics page at all; it only ever
        # redirects to the onboarding.
        assert client.get("/diagnostics", follow_redirects=False).status_code == 303
        return

    page = client.get("/diagnostics")

    assert page.status_code == 200
    assert "Sign-in state" in page.text
    assert "Break-glass (OSD_AUTH_DISABLED)" in page.text
    # Neither surface may leak the hostname, the peer or a proxy network.
    assert "dash.example.com" not in page.text
    assert "127.0.0.1" not in page.text
    # An open setup is a decision, not a broken check or a wrong user count.
    state_section = page.text.split("Sign-in state", maxsplit=1)[1].split("Authentication transport", maxsplit=1)[0]
    assert "status-error" not in state_section


def test_an_unknown_stored_state_is_reported_as_complete_only(review_client):
    db_session, client = review_client
    set_state(db_session, "tampered", auth_enabled="true", hostname="testserver")

    report = build_debug_report(db_session)
    page = client.get("/diagnostics")

    assert "Onboarding state: complete" in report
    assert "tampered" not in report
    assert "tampered" not in page.text


def test_settings_and_account_language_choosers_use_the_shared_locale_source(review_client, monkeypatch):
    db_session, client = review_client
    monkeypatch.setitem(LOCALES, "xx", {**LOCALES["en"], "language.self_name": "Testish"})
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")

    settings_options = language_select(client.get("/settings").text)
    onboarding_options = language_select(client.get("/onboarding").text)

    for option in language_options():
        assert f'value="{option["code"]}"' in settings_options, option
        assert f'value="{option["code"]}"' in onboarding_options, option
    assert settings_options.count("<option") == len(LOCALES)
    assert onboarding_options.count("<option") == len(LOCALES)

    client.post("/settings/auth/enable", data=ACTIVATION, follow_redirects=False)
    client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)
    account_options = language_select(client.get("/account").text)

    assert account_options.count("<option") == len(LOCALES)
    assert 'value="xx"' in account_options
