from datetime import datetime, timedelta

from app.core.time import utc_now
from app.models.core import Insight, InsightRule
from app.services.events import store_event
from app.services.insight_rules import import_ruleset


def _ruleset(rule: dict) -> dict:
    return {
        "schema_version": 1,
        "ruleset_version": "2026-07-11",
        "rules": [rule],
    }


def _pattern_rule(**overrides: object) -> dict:
    rule = {
        "id": "web.test_scanner_wave",
        "title": "Test scanner wave",
        "description": "Scanner wave test.",
        "level": "high",
        "confidence": 0.85,
        "event_types": ["access.error"],
        "path_contains_any": ["/scanner"],
        "group_by": "path",
        "window_minutes": 5,
        "threshold": 3,
        "min_distinct_ips": 2,
    }
    return {**rule, **overrides}


def _store_event(db_session, event_time: datetime, ip: str, *, asset_id: int | None = None) -> None:
    store_event(
        db_session,
        source="test",
        plugin="traefik_log",
        event_type="access.error",
        severity="warning",
        event_time=event_time,
        ip=ip,
        path="/scanner",
        status_code=404,
        asset_id=asset_id,
    )


def test_path_pattern_rule_creates_explainable_cross_ip_insight(db_session):
    import_ruleset(db_session, _ruleset(_pattern_rule()), source="test")
    start = datetime(2026, 7, 11, 12, 0)

    _store_event(db_session, start, "192.0.2.1", asset_id=7)
    _store_event(db_session, start + timedelta(minutes=1), "192.0.2.2", asset_id=7)
    _store_event(db_session, start + timedelta(minutes=2), "192.0.2.3", asset_id=7)
    db_session.commit()

    insight = db_session.query(Insight).filter_by(type="web.test_scanner_wave").one()

    assert insight.ip is None
    assert insight.asset_id == 7
    assert "Matched 3 event(s) from 3 IP(s) within 5 minutes." in insight.description


def test_path_pattern_rule_requires_minimum_distinct_ips(db_session):
    import_ruleset(db_session, _ruleset(_pattern_rule()), source="test")
    start = datetime(2026, 7, 11, 12, 0)

    for offset in range(3):
        _store_event(db_session, start + timedelta(minutes=offset), "192.0.2.1")
    db_session.commit()

    assert db_session.query(Insight).filter_by(type="web.test_scanner_wave").count() == 0


def test_pattern_rule_cooldown_limits_backlog_and_allows_later_wave(db_session):
    import_ruleset(
        db_session,
        _ruleset(_pattern_rule(id="web.test_scanner_cooldown", threshold=1, min_distinct_ips=1)),
        source="test",
    )
    backlog_start = datetime(2020, 1, 1, 12, 0)

    _store_event(db_session, backlog_start, "192.0.2.1")
    _store_event(db_session, backlog_start + timedelta(minutes=1), "192.0.2.2")
    db_session.commit()

    assert db_session.query(Insight).filter_by(type="web.test_scanner_cooldown").count() == 1

    import_ruleset(
        db_session,
        _ruleset(_pattern_rule(id="web.test_scanner_later", threshold=1, min_distinct_ips=1)),
        source="test",
    )
    current_start = utc_now().replace(tzinfo=None)
    _store_event(db_session, current_start, "192.0.2.3")
    _store_event(db_session, current_start + timedelta(minutes=1), "192.0.2.4")
    _store_event(db_session, current_start + timedelta(minutes=6), "192.0.2.4")
    db_session.commit()

    assert db_session.query(Insight).filter_by(type="web.test_scanner_later").count() == 2


def test_ruleset_without_pattern_fields_imports_with_defaults(db_session):
    result = import_ruleset(
        db_session,
        _ruleset(
            {
                "id": "web.legacy_rule",
                "title": "Legacy rule",
                "event_types": ["access.error"],
                "path_contains_any": ["/legacy"],
            }
        ),
        source="test",
    )

    rule = db_session.query(InsightRule).filter_by(rule_id="web.legacy_rule").one()

    assert result["imported"] == 1
    assert rule.group_by == "ip"
    assert rule.min_distinct_ips == 1
