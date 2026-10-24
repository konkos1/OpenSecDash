import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.core import Insight
from app.services import insight_rules
from app.services.events import store_event


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RULESET_PATHS = (
    REPOSITORY_ROOT / "backend/app/insights/rules/default-rules.json",
    REPOSITORY_ROOT / "website/public/rules/insights-rules.json",
)
PROBE_CASES = (
    ("/.env", "web.env_file_probe"),
    ("/app/.env.production", "web.env_file_probe"),
    ("/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php", "web.phpunit_probe"),
    ("/PHPINFO.php", "web.phpinfo_probe"),
    ("/.aws/credentials", "web.credentials_file_probe"),
    ("/.docker/config.json", "web.credentials_file_probe"),
    ("/.npmrc", "web.credentials_file_probe"),
    ("/.pypirc", "web.credentials_file_probe"),
    ("/.htpasswd", "web.credentials_file_probe"),
    ("/.netrc", "web.credentials_file_probe"),
    ("/.kube/config", "web.credentials_file_probe"),
    ("/backup/.kube/config", "web.credentials_file_probe"),
    ("/etc/passwd", "web.system_file_probe"),
    ("/etc/shadow", "web.system_file_probe"),
    ("/.ssh/id_rsa", "web.ssh_file_probe"),
    ("/.ssh/id_ed25519", "web.ssh_file_probe"),
    ("/backup/.ssh/config", "web.ssh_file_probe"),
    ("/.svn/entries", "web.vcs_metadata_probe"),
    ("/.svn/wc.db", "web.vcs_metadata_probe"),
    ("/app/.hg/hgrc", "web.vcs_metadata_probe"),
    ("/wp-config.php.bak", "web.config_backup_probe"),
    ("/wp-config.php.old", "web.config_backup_probe"),
    ("/wp-config.php~", "web.config_backup_probe"),
    ("/config.php.bak", "web.config_backup_probe"),
    ("/config.php.old", "web.config_backup_probe"),
    ("/config.php~", "web.config_backup_probe"),
    ("/actuator/env", "web.spring_actuator_probe"),
    ("/actuator/heapdump", "web.spring_actuator_probe"),
    ("/actuator/configprops", "web.spring_actuator_probe"),
    ("/actuator/logfile", "web.spring_actuator_probe"),
    ("/actuator/threaddump", "web.spring_actuator_probe"),
)


@pytest.mark.parametrize("ruleset_path", RULESET_PATHS, ids=("bundled", "public"))
@pytest.mark.parametrize(("path", "rule_id"), PROBE_CASES)
@pytest.mark.parametrize(("event_type", "status_code"), [("access.denied", 403), ("access.error", 404)])
def test_probe_rules_create_explainable_insights(db_session, ruleset_path, path, rule_id, event_type, status_code):
    insight_rules.import_ruleset(db_session, json.loads(ruleset_path.read_text()), source="test")
    event = store_event(
        db_session,
        source="test",
        plugin="traefik_log",
        event_type=event_type,
        severity="warning",
        ip="192.0.2.1",
        path=path,
        status_code=status_code,
    )
    db_session.commit()

    insight = db_session.query(Insight).filter_by(type=rule_id).one()
    assert insight.ip == "192.0.2.1"
    assert insight.related_event_ids == [event.id]
    assert "Matched 1 event(s) from 1 IP(s) within 5 minutes." in insight.description
    assert {row.type for row in db_session.query(Insight).all()} == {rule_id}


@pytest.mark.parametrize("path", ["/actuator/health", "/index.php", "/upload.php", "/vendor/autoload.php"])
def test_normal_application_paths_do_not_trigger_probe_rules(db_session, path):
    store_event(
        db_session,
        source="test",
        plugin="traefik_log",
        event_type="access.error",
        severity="warning",
        ip="192.0.2.1",
        path=path,
        status_code=404,
    )
    db_session.commit()

    assert db_session.query(Insight).count() == 0


@pytest.mark.parametrize("ruleset_path", RULESET_PATHS, ids=("bundled", "public"))
@pytest.mark.parametrize("path", ["/config.json", "/config.php", "/settings.php", "/login", "/admin", "/administrator"])
@pytest.mark.parametrize(("event_type", "status_code"), [("access.denied", 401), ("access.error", 500)])
def test_repeated_application_errors_do_not_trigger_generic_probe_insights(db_session, ruleset_path, path, event_type, status_code):
    insight_rules.import_ruleset(db_session, json.loads(ruleset_path.read_text()), source="test")
    for _ in range(3):
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type=event_type,
            severity="warning",
            ip="192.0.2.1",
            path=path,
            status_code=status_code,
        )
    db_session.commit()

    assert db_session.query(Insight).count() == 0


@pytest.mark.parametrize("ruleset_path", RULESET_PATHS, ids=("bundled", "public"))
@pytest.mark.parametrize("path", ["/manager/html", "/boaform/admin/formLogin"])
def test_specific_admin_probes_still_require_repeated_requests(db_session, ruleset_path, path):
    insight_rules.import_ruleset(db_session, json.loads(ruleset_path.read_text()), source="test")
    for count in range(1, 4):
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type="access.error",
            severity="warning",
            ip="192.0.2.1",
            path=path,
            status_code=404,
        )
        db_session.commit()
        assert db_session.query(Insight).count() == (1 if count == 3 else 0)


@pytest.mark.parametrize("ruleset_path", RULESET_PATHS, ids=("bundled", "public"))
@pytest.mark.parametrize("path", ["/wp-login.php", "/xmlrpc.php"])
@pytest.mark.parametrize(("event_type", "status_code"), [("access.denied", 401), ("access.error", 404)])
def test_wordpress_probes_require_repeated_requests(db_session, ruleset_path, path, event_type, status_code):
    insight_rules.import_ruleset(db_session, json.loads(ruleset_path.read_text()), source="test")
    for count in range(1, 5):
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type=event_type,
            severity="warning",
            ip="192.0.2.1",
            path=path,
            status_code=status_code,
        )
        db_session.commit()
        assert db_session.query(Insight).count() == (1 if count >= 3 else 0)

    insight = db_session.query(Insight).one()
    assert insight.type == "web.wordpress_scan"
    assert len(insight.related_event_ids) == 3
    assert "Matched 3 event(s) from 1 IP(s) within 5 minutes." in insight.description


@pytest.mark.parametrize("ruleset_path", RULESET_PATHS, ids=("bundled", "public"))
def test_wordpress_probe_threshold_counts_only_recent_requests_from_same_ip(db_session, ruleset_path):
    insight_rules.import_ruleset(db_session, json.loads(ruleset_path.read_text()), source="test")
    start = datetime(2026, 10, 24, 12, 0)
    for offset, ip in [(0, "192.0.2.1"), (6, "192.0.2.2"), (6, "192.0.2.1"), (7, "192.0.2.1")]:
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type="access.error",
            severity="warning",
            event_time=start + timedelta(minutes=offset),
            ip=ip,
            path="/wp-login.php",
            status_code=404,
        )
        db_session.commit()
        assert db_session.query(Insight).count() == 0

    store_event(
        db_session,
        source="test",
        plugin="traefik_log",
        event_type="access.error",
        severity="warning",
        event_time=start + timedelta(minutes=8),
        ip="192.0.2.1",
        path="/xmlrpc.php",
        status_code=404,
    )
    db_session.commit()

    insight = db_session.query(Insight).one()
    assert insight.type == "web.wordpress_scan"
    assert insight.ip == "192.0.2.1"
    assert "Matched 3 event(s) from 1 IP(s) within 5 minutes." in insight.description


@pytest.mark.parametrize("ruleset_path", RULESET_PATHS, ids=("bundled", "public"))
@pytest.mark.parametrize("path", ["/wp-content/themes/site/style.css", "/wp-includes/js/jquery.js", "/wp-admin/", "/wp-admin/admin-ajax.php"])
def test_wordpress_application_errors_do_not_trigger_scan_or_wave(db_session, ruleset_path, path):
    insight_rules.import_ruleset(db_session, json.loads(ruleset_path.read_text()), source="test")
    for index in range(20):
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type="access.error",
            severity="warning",
            ip=f"192.0.2.{index % 5 + 1}",
            path=path,
            status_code=404,
        )
    db_session.commit()

    assert db_session.query(Insight).count() == 0


@pytest.mark.parametrize("ruleset_path", RULESET_PATHS, ids=("bundled", "public"))
def test_wordpress_endpoint_wave_still_creates_cross_ip_insight(db_session, ruleset_path):
    insight_rules.import_bundled_rules(db_session)
    insight_rules.import_ruleset(db_session, json.loads(ruleset_path.read_text()), source="test")
    for index in range(20):
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type="access.error",
            severity="warning",
            ip=f"192.0.2.{index % 5 + 1}",
            path="/wp-login.php" if index % 2 else "/xmlrpc.php",
            status_code=404,
        )
        db_session.commit()
        assert db_session.query(Insight).filter_by(type="web.scanner_wave").count() == (1 if index == 19 else 0)

    insight = db_session.query(Insight).filter_by(type="web.scanner_wave").one()
    assert insight.ip is None
    assert "Matched 20 event(s) from 5 IP(s) within 5 minutes." in insight.description


@pytest.mark.parametrize(("path", "_rule_id"), (*PROBE_CASES, ("/wp-login.php", "web.wordpress_scan"), ("/xmlrpc.php", "web.wordpress_scan")))
def test_successful_probe_paths_do_not_claim_exposure(db_session, path, _rule_id):
    for _ in range(3):
        store_event(
            db_session,
            source="test",
            plugin="traefik_log",
            event_type="access.allowed",
            severity="info",
            ip="192.0.2.1",
            path=path,
            status_code=200,
        )
    db_session.commit()

    assert db_session.query(Insight).count() == 0


def test_public_rules_match_bundled_rules_without_unreleased_path_grouping():
    bundled, public = [json.loads(path.read_text()) for path in RULESET_PATHS]

    assert public["rules"] == [rule for rule in bundled["rules"] if rule["id"] != "web.scanner_wave"]
    assert all(rule.get("group_by", "ip") == "ip" for rule in public["rules"])


def test_published_manifest_and_ruleset_refresh_together(monkeypatch, db_session):
    class PublishedResponse:
        status_code = 200
        headers = {}

        def __init__(self, path):
            self.content = path.read_bytes()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            for start in range(0, len(self.content), chunk_size):
                yield self.content[start : start + chunk_size]

    def published_get(url, timeout, headers=None, stream=False):
        assert stream is True
        if url == insight_rules.RULE_MANIFEST_URL:
            return PublishedResponse(REPOSITORY_ROOT / "website/public/rules/insights-rules-v1.sha256.json")
        assert url == insight_rules.RULE_SOURCE_URL
        return PublishedResponse(RULESET_PATHS[1])

    monkeypatch.setattr(insight_rules.requests, "get", published_get)
    monkeypatch.setattr(insight_rules, "utc_now", lambda: datetime(2027, 4, 30, 12, tzinfo=UTC))

    result = insight_rules.refresh_insight_rules(db_session, force=True)

    assert result["status"] == "updated"
    assert result["version"] == "2026-10-24"
    active = {rule.id: rule for rule in insight_rules.active_rules(db_session)}
    assert active["web.scanner_wave"].source == "bundled"
    for _path, rule_id in PROBE_CASES:
        assert active[rule_id].source == "remote"


def test_published_manifest_expires_after_renewed_deadline(monkeypatch):
    manifest = json.loads((REPOSITORY_ROOT / "website/public/rules/insights-rules-v1.sha256.json").read_text())
    assert manifest["sha256"] == hashlib.sha256(RULESET_PATHS[1].read_bytes()).hexdigest()
    monkeypatch.setattr(insight_rules, "utc_now", lambda: datetime(2027, 5, 1, tzinfo=UTC))

    with pytest.raises(ValueError, match="expired on 2027-04-30"):
        insight_rules._expected_ruleset_hash(manifest)
