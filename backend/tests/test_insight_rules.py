from app.models.core import Diagnostic, Insight
from app.models.settings import Setting
from app.services.events import store_event
import pytest

from app.services.insight_rules import RULE_SOURCE_URL, parse_rules, refresh_insight_rules


class _Response:
    status_code = 200
    headers = {"ETag": "test-etag"}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "schema_version": 1.1,
            "ruleset_version": "2026-07-02",
            "rules": [
                {
                    "id": "web.test_probe",
                    "title": "Test probe",
                    "description": "Test probe description.",
                    "level": "medium",
                    "confidence": 0.7,
                    "event_types": ["access.error"],
                    "path_contains_any": ["/test-probe"],
                    "group_by": "ip",
                    "window_minutes": 5,
                    "threshold": 1,
                }
            ],
        }


def test_default_declarative_rules_create_web_insight(db_session):
    store_event(
        db_session,
        source="test",
        plugin="traefik_log",
        event_type="access.error",
        severity="warning",
        ip="8.8.8.8",
        path="/wp-login.php",
        status_code=404,
    )
    db_session.commit()

    insight = db_session.query(Insight).filter_by(type="web.wordpress_scan").one()

    assert insight.title == "Possible WordPress scan"
    assert insight.ip == "8.8.8.8"


def test_refresh_insight_rules_uses_hardcoded_source_url(monkeypatch, db_session):
    calls = []

    def fake_get(url, timeout, headers):
        calls.append((url, timeout, headers))
        return _Response()

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)

    result = refresh_insight_rules(db_session, force=True)

    assert result == {"status": "updated", "source": "remote", "version": "2026-07-02", "count": 6}
    assert calls[0][0] == RULE_SOURCE_URL
    assert db_session.query(Setting).filter_by(key="insight_rules.version").one().value == "2026-07-02"


def _ruleset_diagnostic(db_session):
    return db_session.query(Diagnostic).filter_by(plugin="insight_rules", component="ruleset").one()


def test_refresh_insight_rules_reports_bundled_only_fallback_on_first_ever_failure(monkeypatch, db_session):
    # No RULE_FETCHED_AT_KEY/RULE_VERSION_KEY yet - a remote fetch has never
    # succeeded, so the fallback is unambiguously "bundled rules only".
    def fake_get(url, timeout, headers):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get)

    result = refresh_insight_rules(db_session, force=True)

    assert result["status"] == "failed"
    diagnostic = _ruleset_diagnostic(db_session)
    assert diagnostic.status == "warning"
    assert "pre-shipped rules only" in diagnostic.last_error
    assert "last known-good remote" not in diagnostic.last_error


def test_refresh_insight_rules_reports_last_known_good_remote_on_later_failure(monkeypatch, db_session):
    # A previous fetch succeeded (version + timestamp already stored); a
    # later failure must say so precisely instead of the ambiguous old
    # "using database/bundled rules" message.
    monkeypatch.setattr("app.services.insight_rules.requests.get", lambda url, timeout, headers: _Response())
    first = refresh_insight_rules(db_session, force=True)
    assert first["status"] == "updated"

    def fake_get_fails(url, timeout, headers):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("app.services.insight_rules.requests.get", fake_get_fails)
    result = refresh_insight_rules(db_session, force=True)

    assert result["status"] == "failed"
    assert result["version"] == "2026-07-02"
    diagnostic = _ruleset_diagnostic(db_session)
    assert diagnostic.status == "warning"
    assert "last known-good remote rules: v2026-07-02" in diagnostic.last_error
    assert "plus pre-shipped rules" in diagnostic.last_error


def test_schema_version_allows_same_major_minor_updates():
    rules = parse_rules(
        {
            "schema_version": 1.1,
            "ruleset_version": "2026-07-02",
            "rules": [
                {
                    "id": "web.test_probe",
                    "title": "Test probe",
                    "event_types": ["access.error"],
                    "path_contains_any": ["/test"],
                }
            ],
        }
    )

    assert rules[0].id == "web.test_probe"


def test_schema_version_rejects_breaking_major_updates():
    with pytest.raises(ValueError, match="Unsupported insight rules schema_version"):
        parse_rules(
            {
                "schema_version": 2,
                "ruleset_version": "2026-07-02",
                "rules": [
                    {
                        "id": "web.test_probe",
                        "title": "Test probe",
                        "event_types": ["access.error"],
                        "path_contains_any": ["/test"],
                    }
                ],
            }
        )
