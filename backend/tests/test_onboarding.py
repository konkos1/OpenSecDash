"""First-admin onboarding, its access mode and the shared locale source (plan phase 2)."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.api import auth as auth_api
from app.core.i18n import language_options
from app.database.base import Base
from app.database.dependencies import get_db
from app.locales import LOCALES
from app.main import app
from app.models.settings import Setting
from app.models.users import User, UserPreference, UserSession
from app.services.auth import (
    AUTH_ENABLED_SETTING,
    AUTH_HOSTNAME_SETTING,
    AUTH_ONBOARDING_COMPLETE,
    AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED,
    AUTH_ONBOARDING_PENDING,
    AUTH_ONBOARDING_STATE_SETTING,
    create_user,
)
from app.services.onboarding import complete_onboarding
from app.services.user_preferences import normalize_preferences
from app.web import auth as auth_web

# The pending onboarding closes event websockets with the standard policy code,
# before any event data is read or sent.
PENDING_WEBSOCKET_CLOSE_CODE = 1008

VALID_FORM = {
    "language": "en",
    "hostname": "testserver",
    "username": "admin",
    "password": "password123",
    "password_confirm": "password123",
}


@pytest.fixture()
def onboarding_client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'onboarding.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session = session_factory()

    def get_test_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(auth_web, "SessionLocal", session_factory)
    monkeypatch.setattr("app.main.SessionLocal", session_factory)
    monkeypatch.setattr("app.main.init_db", lambda: None)
    app.dependency_overrides[get_db] = get_test_db
    auth_api.reset_login_backoff()
    client = TestClient(app, base_url="https://testserver")
    try:
        yield db_session, client, session_factory
    finally:
        client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        db_session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def set_state(db_session, state, *, auth_enabled="true"):
    """Seed the settings a started installation carries in one of the two open states."""
    db_session.add_all(
        [
            Setting(key=AUTH_ENABLED_SETTING, value=auth_enabled),
            Setting(key=AUTH_ONBOARDING_STATE_SETTING, value=state),
        ]
    )
    db_session.commit()


def value(db_session, key):
    db_session.expire_all()
    setting = db_session.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting is not None else None


def language_select(page_text):
    """Return the markup of the language chooser only."""
    return page_text.split('name="language"', maxsplit=1)[1].split("</select>", maxsplit=1)[0]


def test_pending_redirects_pages_and_blocks_apis_login_and_plugins(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding"

    for path in ("/settings", "/login", "/auth/oidc/login", "/account", "/diagnostics", "/access", "/instance/logo"):
        blocked = client.get(path, follow_redirects=False)
        assert blocked.status_code == 303, path
        assert blocked.headers["location"] == "/onboarding", path

    api = client.get("/api/events")
    assert api.status_code == 503
    assert api.json() == {"detail": "Setup required"}


def test_pending_keeps_health_ready_onboarding_and_static_assets_reachable(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/js/onboarding.js").status_code == 200
    page = client.get("/onboarding")
    assert page.status_code == 200
    assert "Set up OpenSecDash" in page.text


def test_pending_closes_the_event_websocket_without_data(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("wss://testserver/ws/events"):
            pass

    assert exc_info.value.code == PENDING_WEBSOCKET_CLOSE_CODE


def test_unprotected_http_shows_the_form_but_cannot_complete_it(onboarding_client):
    db_session, _client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    # No context manager: the application lifespan must never run against the
    # developer's own database from a test.
    http_client = TestClient(app, base_url="http://testserver")
    try:
        assert http_client.get("/", follow_redirects=False).headers["location"] == "/onboarding"
        page = http_client.get("/onboarding")
        assert page.status_code == 200
        assert "Set up OpenSecDash" in page.text

        response = http_client.post("/onboarding", data=VALID_FORM, follow_redirects=False)
    finally:
        http_client.close()

    assert response.status_code == 400
    assert "Open this page through the trusted reverse proxy using HTTPS." in response.text
    assert db_session.query(User).count() == 0
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING


def test_completion_needs_the_explicitly_trusted_https_443_hostname_edge(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    untrusted_client = TestClient(app, base_url="https://testserver", client=("203.0.113.5", 1234))
    try:
        untrusted = untrusted_client.post("/onboarding", data=VALID_FORM, follow_redirects=False)
    finally:
        untrusted_client.close()
    assert untrusted.status_code == 400
    assert "OSD_TRUSTED_PROXIES" in untrusted.text

    wrong_port = client.post("/onboarding", data=VALID_FORM, headers={"x-forwarded-port": "8443"}, follow_redirects=False)
    assert wrong_port.status_code == 400
    assert "port 443" in wrong_port.text

    wrong_host = client.post("/onboarding", data={**VALID_FORM, "hostname": "other.example"}, follow_redirects=False)
    assert wrong_host.status_code == 400
    assert "does not match" in wrong_host.text

    assert db_session.query(User).count() == 0
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING
    assert value(db_session, AUTH_HOSTNAME_SETTING) is None


def test_invalid_input_changes_nothing(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    rejected = [
        client.post("/onboarding", data={**VALID_FORM, "password_confirm": "other-password"}, follow_redirects=False),
        client.post("/onboarding", data={**VALID_FORM, "password": "short", "password_confirm": "short"}, follow_redirects=False),
        client.post("/onboarding", data={**VALID_FORM, "username": "Not A Username"}, follow_redirects=False),
        client.post("/onboarding", data={**VALID_FORM, "hostname": "https://dash.example.com/"}, follow_redirects=False),
        client.post("/onboarding", data={**VALID_FORM, "language": "zz"}, follow_redirects=False),
    ]

    assert [response.status_code for response in rejected] == [400] * 5
    assert db_session.query(User).count() == 0
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING
    assert value(db_session, AUTH_HOSTNAME_SETTING) is None


def test_cross_site_post_is_rejected_by_the_existing_origin_policy(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    response = client.post("/onboarding", data=VALID_FORM, headers={"origin": "https://evil.example"}, follow_redirects=False)

    assert response.status_code == 403
    assert db_session.query(User).count() == 0
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING


def test_successful_completion_creates_one_admin_and_leads_to_the_normal_login(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    response = client.post("/onboarding", data={**VALID_FORM, "language": "de"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "set-cookie" not in response.headers
    assert db_session.query(UserSession).count() == 0

    admins = db_session.query(User).all()
    assert len(admins) == 1
    assert admins[0].username == "admin"
    assert admins[0].role == "admin"
    assert admins[0].is_active is True
    assert admins[0].last_login_at is None
    assert value(db_session, AUTH_HOSTNAME_SETTING) == "testserver"
    assert value(db_session, AUTH_ENABLED_SETTING) == "true"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE
    # The chosen language becomes the global default and the first admin's own
    # start language, in the same transaction.
    assert value(db_session, "language") == "de"
    preferences = db_session.query(UserPreference).filter(UserPreference.user_id == admins[0].id).first()
    assert preferences is not None and preferences.language == "de"

    login = client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)
    assert login.status_code == 303
    assert "osd_session" in login.headers["set-cookie"]
    assert client.get("/").status_code == 200


def test_two_parallel_first_visitors_create_exactly_one_admin(onboarding_client):
    db_session, _client, session_factory = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)
    db_session.close()

    def claim(username):
        db = session_factory()
        try:
            return complete_onboarding(db, language="en", hostname="testserver", username=username, password="password123")
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("first", "second")))

    verify = session_factory()
    try:
        assert results.count(None) == 1
        assert verify.query(User).count() == 1
        assert verify.query(Setting).filter(Setting.key == AUTH_ONBOARDING_STATE_SETTING).first().value == AUTH_ONBOARDING_COMPLETE
    finally:
        verify.close()


def test_completed_or_inconsistent_onboarding_creates_no_user(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_COMPLETE)
    db_session.add(Setting(key=AUTH_HOSTNAME_SETTING, value="testserver"))
    db_session.commit()

    # With a completed onboarding the normal authentication takes over again, so
    # an anonymous request never even reaches the setup route.
    completed = client.post("/onboarding", data=VALID_FORM, follow_redirects=False)
    assert completed.status_code == 303
    assert completed.headers["location"].startswith("/login")
    assert db_session.query(User).count() == 0

    # Pending with existing accounts is inconsistent: it must not adopt or pick
    # one of them, and it must not create another admin either.
    state = db_session.query(Setting).filter(Setting.key == AUTH_ONBOARDING_STATE_SETTING).first()
    state.value = AUTH_ONBOARDING_PENDING
    create_user(db_session, "existing", "password123", "viewer")
    db_session.commit()

    inconsistent = client.post("/onboarding", data=VALID_FORM, follow_redirects=False)
    assert inconsistent.status_code == 400
    assert db_session.query(User).count() == 1
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_PENDING


def test_onboarding_page_shows_no_navigation_plugin_or_instance_data(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)
    db_session.add_all(
        [
            Setting(key="instance_description", value="Konkos homelab"),
            Setting(key="domain", value="internal.example"),
            Setting(key="plugin.traefik_log.enabled", value="true"),
        ]
    )
    db_session.commit()

    page = client.get("/onboarding")

    assert page.status_code == 200
    for leak in ("Konkos homelab", "internal.example", "/instance/logo", "/instance/favicon", "Dashboard", "Rollups", "/access", "127.0.0.1"):
        assert leak not in page.text, leak


def test_onboarding_page_explains_the_deliberate_bypass_without_offering_it(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    page = client.get("/onboarding")

    assert "OSD_AUTH_DISABLED=true" in page.text
    assert "restart OpenSecDash" in page.text
    assert "full access to all pages, APIs and actions" in page.text
    assert "external authentication proxy" in page.text
    assert "this page comes back" in page.text
    assert "has to stay set permanently" in page.text
    assert "stays visible on every page" in page.text
    # Nothing on this page may set the variable or mark the setup as skipped.
    assert page.text.count("<form") == 2
    assert 'action="/onboarding"' in page.text
    assert "skip" not in page.text.lower()


def test_legacy_review_blocks_nothing_and_can_be_completed_without_an_account(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()

    assert client.get("/").status_code == 200
    assert client.get("/settings").status_code == 200
    assert client.get("/api/events").status_code == 200
    assert client.get("/access").status_code == 200
    with client.websocket_connect("wss://testserver/ws/events") as websocket:
        assert websocket.receive_json()["type"] == "connected"

    page = client.get("/onboarding")
    assert page.status_code == 200
    assert "Repeat password" in page.text

    response = client.post("/onboarding", data=VALID_FORM, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "set-cookie" not in response.headers
    assert db_session.query(UserSession).count() == 0
    assert db_session.query(User).count() == 1
    assert value(db_session, AUTH_ENABLED_SETTING) == "true"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE


def test_legacy_review_with_an_existing_admin_only_confirms_the_hostname(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")
    existing = create_user(db_session, "olduser", "password123", "admin")
    db_session.commit()
    password_hash = existing.password_hash

    page = client.get("/onboarding")
    assert "Repeat password" not in page.text
    assert "olduser" not in page.text

    submitted_account = client.post("/onboarding", data=VALID_FORM, follow_redirects=False)
    assert submitted_account.status_code == 400
    assert "Accounts cannot be created or changed here." in submitted_account.text

    response = client.post("/onboarding", data={"language": "en", "hostname": "testserver"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "set-cookie" not in response.headers
    db_session.expire_all()
    users = db_session.query(User).all()
    assert len(users) == 1
    assert users[0].username == "olduser"
    assert users[0].role == "admin"
    assert users[0].password_hash == password_hash
    assert db_session.query(UserSession).count() == 0
    assert value(db_session, AUTH_HOSTNAME_SETTING) == "testserver"
    assert value(db_session, AUTH_ENABLED_SETTING) == "true"
    assert value(db_session, AUTH_ONBOARDING_STATE_SETTING) == AUTH_ONBOARDING_COMPLETE


def test_legacy_review_intro_addresses_the_upgraded_installation(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")
    create_user(db_session, "olduser", "password123", "admin")
    db_session.commit()

    page = client.get("/onboarding")

    assert "This installation is still open after the update." in page.text
    assert "An administrator already exists." in page.text
    assert "This installation has no administrator yet." not in page.text


def test_pending_intro_addresses_the_new_installation(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    page = client.get("/onboarding")

    assert "This installation has no administrator yet." in page.text
    assert "This installation is still open after the update." not in page.text
    assert "An administrator already exists." not in page.text


def test_inactive_operator_and_viewer_accounts_do_not_count_as_an_admin(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_LEGACY_REVIEW_REQUIRED, auth_enabled="false")
    create_user(db_session, "viewer", "password123", "viewer")
    inactive_admin = create_user(db_session, "inactive", "password123", "admin")
    inactive_admin.is_active = False
    db_session.commit()

    page = client.get("/onboarding")
    assert "Repeat password" in page.text

    response = client.post("/onboarding", data=VALID_FORM, follow_redirects=False)

    assert response.status_code == 303
    db_session.expire_all()
    assert {user.username for user in db_session.query(User).all()} == {"viewer", "inactive", "admin"}
    assert db_session.query(User).filter(User.username == "inactive").first().is_active is False


def test_language_chooser_uses_the_locale_registry_and_renders_one_language(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    english = client.get("/onboarding")
    options = language_select(english.text)
    assert options.count("<option") == len(LOCALES)
    for option in language_options():
        assert f'value="{option["code"]}"' in options
        assert f">{option['label']}</option>" in options

    german = client.get("/onboarding?language=de")
    assert '<html lang="de">' in german.text
    assert "OpenSecDash einrichten" in german.text
    assert "Set up OpenSecDash" not in german.text

    unknown = client.get("/onboarding?language=zz")
    assert '<html lang="en">' in unknown.text
    assert "Set up OpenSecDash" in unknown.text
    # Rendering or switching must not write the global language setting.
    assert value(db_session, "language") is None


def test_language_form_is_a_separate_get_without_credentials(onboarding_client):
    db_session, client, _ = onboarding_client
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    page = client.get("/onboarding")
    language_form = page.text.split('<form method="get"', maxsplit=1)[1].split("</form>", maxsplit=1)[0]

    assert 'action="/onboarding"' in language_form
    assert "username" not in language_form
    assert "password" not in language_form


def test_a_newly_registered_core_locale_needs_no_second_allowlist(onboarding_client, monkeypatch):
    db_session, client, _ = onboarding_client
    monkeypatch.setitem(LOCALES, "xx", {**LOCALES["en"], "language.self_name": "Testish", "onboarding.title": "Set up in Testish"})
    set_state(db_session, AUTH_ONBOARDING_PENDING)

    page = client.get("/onboarding?language=xx")
    assert '<html lang="xx">' in page.text
    assert "Set up in Testish" in page.text
    assert 'value="xx"' in language_select(page.text)
    assert normalize_preferences({"language": "xx"})["language"] == "xx"

    # Settings shows the same options while the installation is still open, and
    # the account page does once the first admin is signed in.
    state = db_session.query(Setting).filter(Setting.key == AUTH_ONBOARDING_STATE_SETTING).first()
    state.value = AUTH_ONBOARDING_COMPLETE
    db_session.query(Setting).filter(Setting.key == AUTH_ENABLED_SETTING).first().value = "false"
    db_session.commit()
    assert 'value="xx"' in language_select(client.get("/settings").text)

    db_session.query(Setting).filter(Setting.key == AUTH_ENABLED_SETTING).first().value = "true"
    db_session.add(Setting(key=AUTH_HOSTNAME_SETTING, value="testserver"))
    create_user(db_session, "admin", "password123", "admin")
    db_session.commit()
    client.post("/login", data={"username": "admin", "password": "password123"}, follow_redirects=False)
    assert 'value="xx"' in language_select(client.get("/account").text)
