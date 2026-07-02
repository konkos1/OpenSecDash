from app.models.core import Insight
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
