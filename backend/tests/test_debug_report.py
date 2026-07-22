import io
from datetime import timedelta
import zipfile

from app.api.pages import build_debug_report, build_debug_report_zip
from app.core.logging import redact_sensitive, redacted_setting_value
from app.core.time import utc_now
from app.core.version import get_app_version
from app.models.core import Diagnostic, Notification, NotificationRule, PluginRecord
from app.models.saved_views import SavedView
from app.models.settings import InstanceFile, Setting
from app.models.users import ExternalIdentity, User, UserPreference, UserSession


def test_debug_report_includes_docker_log_hint_when_file_logging_disabled(db_session):
    db_session.add(Setting(key="log_file_enabled", value="false"))
    db_session.commit()

    report = build_debug_report(db_session)

    assert "File logging is disabled" in report
    assert "docker compose logs opensecdash --tail=500" in report


def test_debug_redaction_keeps_auth_switch_and_partially_masks_email_addresses():
    assert redacted_setting_value("auth.enabled", "true") == "true"
    assert redacted_setting_value("notifications.smtp_password", "secret") == "<redacted>"
    assert redact_sensitive("OpenSecDash <sender@example.com>, admin@example") == "OpenSecDash <s***@e***.com>, a***@e***"


def test_debug_report_redacts_sensitive_settings_and_log_tail(db_session, tmp_path):
    log_file = tmp_path / "opensecdash.log"
    log_file.write_text(
        "normal line\n"
        "token=super-secret\n"
        "Authorization: Bearer abc123\n"
        "https://user:pass@example.com/path?token=query-secret&ok=yes\n",
        encoding="utf-8",
    )
    db_session.add_all(
        [
            Setting(key="log_file_enabled", value="true"),
            Setting(key="log_file_path", value=str(log_file)),
            Setting(key="asset_updates.github_token", value="plain-secret-token"),
            Setting(key="domain", value="homelab.example"),
            PluginRecord(id="test_plugin", name="Test Plugin", version="1.0.0", capabilities=["datasource"], status="healthy"),
            Diagnostic(plugin="test_plugin", component="plugin", status="healthy"),
            Diagnostic(plugin="system", component="database_migrations", status="healthy", last_error="Database schema is up to date."),
            User(username="private-admin", password_hash="password-hash", role="admin", is_active=True),
            UserPreference(user_id=1),
            UserSession(token_hash="a" * 64, user_id=1, expires_at=utc_now().replace(tzinfo=None) + timedelta(days=1)),
            SavedView(user_id=1, name="Private investigation", scope="events", filter_json={"country": "DE"}),
            NotificationRule(rule_id="core.test", name="Test rule", enabled=True),
            Notification(
                rule_id="core.test",
                channel="email",
                status="failed",
                error="SMTP rejected recipient@example.com; token=notification-secret unavailable",
            ),
            InstanceFile(kind="logo", filename="private-logo.png", content_type="image/png", data=b"test-image", updated_at=1),
            Setting(key="auth.enabled", value="true"),
            Setting(key="notifications.enabled", value="true"),
            Setting(key="notifications.smtp_host", value="smtp.example.com"),
            Setting(key="notifications.smtp_sender", value="sender@example.com"),
            Setting(key="notifications.smtp_recipient", value="recipient@example.com"),
            Setting(key="notifications.smtp_password", value="smtp-secret"),
            Setting(key="ui.dashboard_layout.user.1", value='[{"id":"core.top_insights","visible":true}]'),
        ]
    )
    db_session.commit()

    report = build_debug_report(db_session)

    assert "OpenSecDash Debug Package" in report
    assert f"OpenSecDash version: {get_app_version()}" in report
    assert "Redaction notice" in report
    assert "email addresses" in report
    assert "asset_updates.github_token: <redacted>" in report
    assert "auth.enabled: true" in report
    assert "notifications.smtp_sender: s***@e***.com" in report
    assert "notifications.smtp_recipient: r***@e***.com" in report
    assert "sender@example.com" not in report
    assert "recipient@example.com" not in report
    assert "plain-secret-token" not in report
    assert "super-secret" not in report
    assert "abc123" not in report
    assert "query-secret" not in report
    assert "token=<redacted>" in report
    assert "Authorization=<redacted>" in report
    assert "https://<redacted>@example.com/path?token=<redacted>" in report
    assert "homelab.example" in report

    zip_bytes = build_debug_report_zip(db_session)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = set(archive.namelist())
        assert {
            "README.txt",
            "settings.txt",
            "plugins.txt",
            "diagnostics.txt",
            "datasources.txt",
            "database-counts.txt",
            "runtime-environment.txt",
            "authentication.txt",
            "notifications.txt",
            "branding-pwa.txt",
            "ui-state.txt",
            "recent-actions.txt",
            "opensecdash-log.txt",
        }.issubset(names)
        settings = archive.read("settings.txt").decode("utf-8")
        log_tail = archive.read("opensecdash-log.txt").decode("utf-8")
        authentication = archive.read("authentication.txt").decode("utf-8")
        notifications = archive.read("notifications.txt").decode("utf-8")
        branding = archive.read("branding-pwa.txt").decode("utf-8")
        ui_state = archive.read("ui-state.txt").decode("utf-8")

    assert "asset_updates.github_token: <redacted>" in settings
    assert "plain-secret-token" not in settings
    assert "super-secret" not in log_tail
    assert "token=<redacted>" in log_tail
    assert "Effective authentication: enabled" in authentication
    assert "Users role admin: 1" in authentication
    assert "Active sessions: 1" in authentication
    assert "private-admin" not in authentication
    assert "Deliveries failed: 1" in notifications
    assert "notification-secret" not in notifications
    assert "r***@e***.com" in notifications
    assert "token=<redacted>" in notifications
    assert "payload" not in notifications
    assert "Logo: configured; content_type=image/png; bytes=10" in branding
    assert "private-logo.png" not in branding
    assert "Saved views events: 1" in ui_state
    assert "Dashboard layouts: 1" in ui_state
    assert "Private investigation" not in ui_state
    assert '"country": "DE"' not in ui_state


def test_debug_report_aggregates_external_identities_without_issuers_or_subjects(db_session):
    issuer = "https://idp.example/realms/homelab"
    db_session.add_all(
        [
            User(username="local-admin", password_hash="password-hash", role="admin", is_active=True),
            User(username="oidc-viewer", password_hash=None, role="viewer", is_active=True),
            ExternalIdentity(user_id=2, provider="oidc", issuer=issuer, subject="private-subject"),
            UserSession(
                token_hash="a" * 64,
                user_id=1,
                expires_at=utc_now().replace(tzinfo=None) + timedelta(days=1),
                auth_method="password",
            ),
            UserSession(
                token_hash="b" * 64,
                user_id=2,
                expires_at=utc_now().replace(tzinfo=None) + timedelta(days=1),
                auth_method="oidc",
            ),
        ]
    )
    db_session.commit()

    report = build_debug_report(db_session)

    assert "Users without local password: 1" in report
    assert "External identities: 1" in report
    assert "Sessions method password: 1" in report
    assert "Sessions method oidc: 1" in report
    assert "external_identities: 1" in report
    assert issuer not in report
    assert "private-subject" not in report


def test_debug_report_summarizes_environment_without_exposing_proxy_networks(db_session, monkeypatch):
    monkeypatch.setenv("OSD_TRUSTED_PROXIES", "192.168.10.0/24,invalid-network")
    monkeypatch.setenv("OSD_AUTH_DISABLED", "true")
    db_session.add(Setting(key="auth.enabled", value="true"))
    db_session.commit()

    report = build_debug_report(db_session)

    assert "Trusted proxy mode: custom; valid_entries=1; invalid_entries=1" in report
    assert "192.168.10.0/24" not in report
    assert "Effective authentication: disabled" in report
    assert "Break-glass override: active" in report
