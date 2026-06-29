import io
import zipfile

from app.api.pages import build_debug_report, build_debug_report_zip
from app.models.core import Diagnostic, PluginRecord
from app.models.settings import Setting


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
            Setting(key="github_token", value="plain-secret-token"),
            Setting(key="domain", value="homelab.example"),
            PluginRecord(id="test_plugin", name="Test Plugin", version="1.0.0", capabilities=["datasource"], status="healthy"),
            Diagnostic(plugin="test_plugin", component="plugin", status="healthy"),
        ]
    )
    db_session.commit()

    report = build_debug_report(db_session)

    assert "OpenSecDash Debug Package" in report
    assert "Redaction notice" in report
    assert "github_token: <redacted>" in report
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
            "recent-actions.txt",
            "opensecdash-log.txt",
        }.issubset(names)
        settings = archive.read("settings.txt").decode("utf-8")
        log_tail = archive.read("opensecdash-log.txt").decode("utf-8")

    assert "github_token: <redacted>" in settings
    assert "plain-secret-token" not in settings
    assert "super-secret" not in log_tail
    assert "token=<redacted>" in log_tail
