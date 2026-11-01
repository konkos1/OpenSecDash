from datetime import datetime, timedelta

import pytest

from app.models.core import Insight, InsightRule
from app.models.events import Event
from app.services.insight_rules import active_rules, apply_declarative_insight_rules, import_ruleset, parse_rules


def payload(**changes):
    rule = {"id": "test.v2", "title": "Test probe", "kind": "web_probe",
            "event_types": ["access.error"], "match_options": {"path_equals_any": ["/phpinfo.php"]}}
    return {"schema_version": 2, "rules": [{**rule, **changes}]}


def test_schema_two_exact_only_and_chain_defaults():
    assert parse_rules(payload())[0].path_contains_any == ()
    rule = parse_rules(payload(kind="scan_ban_chain", group_by="path", min_distinct_ips=2))[0]
    assert rule.kind == "scan_ban_chain"
    assert rule.match_options == {"path_equals_any": ["/phpinfo.php"], "min_distinct_assets": 2,
                                  "min_distinct_countries": 2, "followup_seconds": 60}


@pytest.mark.parametrize("changes", [
    {"kind": "code"}, {"match_options": {"unknown": True}}, {"match_options": {}},
    {"match_options": {"path_equals_any": []}},
    *[{"match_options": {"path_equals_any": ["/x"], key: value}}
      for key, values in {"status_codes": [[], [True], [99], [600], [404, 404], list(range(100, 201))],
                          "methods": [[], ["INVALID"], [True], ["GET"] * 11],
                          "path_prefix_any": [[""], ["x" * 201], ["/x"] * 51],
                          "followup_seconds": [1]}.items() for value in values],
    *[{"kind": "scan_ban_chain", "group_by": "path", "min_distinct_ips": 2,
       "match_options": {"path_equals_any": ["/x"], key: value}}
      for key, values in {"min_distinct_assets": [1, 101, True], "min_distinct_countries": [0, 101, True],
                          "followup_seconds": [0, 301, True], "ban_event_type": ["security.ban.manual"]}.items()
      for value in values],
    {"kind": "scan_ban_chain", "group_by": "ip", "min_distinct_ips": 2},
    {"kind": "scan_ban_chain", "group_by": "path", "min_distinct_ips": 1},
    {"kind": "scan_ban_chain", "group_by": "path", "min_distinct_ips": 2, "window_minutes": 61},
    {"kind": "scan_ban_chain", "group_by": "path", "min_distinct_ips": 2, "event_types": ["security.ban"]},
])
def test_invalid_schema_two_is_atomic(db_session, changes):
    with pytest.raises(ValueError):
        import_ruleset(db_session, payload(**changes), source="test")
    assert db_session.query(InsightRule).count() == 0


def test_chain_rule_limit():
    data = payload(kind="scan_ban_chain", group_by="path", min_distinct_ips=2)
    data["rules"] = [{**data["rules"][0], "id": f"test.{i}"} for i in range(20)]
    assert len(parse_rules(data)) == 20
    data["rules"].append({**data["rules"][0], "id": "test.extra"})
    with pytest.raises(ValueError):
        parse_rules(data)


@pytest.mark.parametrize("operator,path,expected", [
    ("path_equals_any", "/A%_?x", True), ("path_equals_any", "/a%_?x/extra", False),
    ("path_prefix_any", "/A%_?x/extra", True), ("path_prefix_any", "/za%_?x", False),
    ("path_contains_any", "/z/a%_?x/extra", True), ("path_contains_any", "/aZZZq?x", False),
    ("path_equals_any", "/a%25_?x", False),
])
def test_path_operators_have_literal_case_insensitive_matching(db_session, operator, path, expected):
    changes = {"match_options": {operator: ["/a%_?x"]}} if operator != "path_contains_any" else {
        "path_contains_any": ["/a%_?x"], "match_options": {}}
    import_ruleset(db_session, payload(**changes), source="test")
    event = Event(event_type="access.error", ip="192.0.2.1", path=path, event_time=datetime(2026, 11, 1))
    db_session.add(event)
    db_session.flush()
    apply_declarative_insight_rules(db_session, event)
    assert db_session.query(Insight).count() == int(expected)


@pytest.mark.parametrize("status,method,expected", [(404, "GET", True), (404, "get", True),
    (403, "GET", False), (404, "POST", False), (404, None, False), (None, "GET", False)])
def test_path_or_and_status_method_and(db_session, status, method, expected):
    import_ruleset(db_session, payload(path_contains_any=["/contains"], match_options={
        "path_equals_any": ["/exact"], "path_prefix_any": ["/prefix"],
        "status_codes": [404], "methods": ["get"]}), source="test")
    for path in ("/contains", "/exact", "/prefix/child"):
        db_session.query(Insight).delete()
        event = Event(event_type="access.error", ip="192.0.2.1", path=path, status_code=status,
                      method=method, event_time=datetime(2026, 11, 1))
        db_session.add(event)
        db_session.flush()
        apply_declarative_insight_rules(db_session, event)
        assert db_session.query(Insight).count() == int(expected)


def test_reimport_schema_one_clears_schema_two_options(db_session):
    import_ruleset(db_session, payload(kind="scan_ban_chain", group_by="path", min_distinct_ips=2), source="test")
    data = payload(path_contains_any=["/old"])
    data["schema_version"] = 1
    import_ruleset(db_session, data, source="test")
    rule = active_rules(db_session)[0]
    assert rule.kind == "web_probe" and rule.match_options is None
    row = db_session.query(InsightRule).one()
    assert row.kind == "web_probe" and row.match_options is None


def test_chain_is_not_evaluated_synchronously(db_session):
    import_ruleset(db_session, payload(kind="scan_ban_chain", group_by="path", min_distinct_ips=2), source="test")
    for index in range(3):
        event = Event(event_type="access.error", ip=f"192.0.2.{index + 1}", path="/phpinfo.php",
                      event_time=datetime(2026, 11, 1))
        db_session.add(event)
        db_session.flush()
        apply_declarative_insight_rules(db_session, event)
    assert db_session.query(Insight).count() == 0


def test_counts_and_newest_twenty_use_same_filters(db_session):
    import_ruleset(db_session, payload(threshold=30, group_by="path", min_distinct_ips=2,
        match_options={"path_equals_any": ["/phpinfo.php"], "methods": ["GET"], "status_codes": [404]}), source="test")
    start = datetime(2026, 11, 1)
    events = []
    for index in reversed(range(30)):
        event = Event(event_type="access.error", ip=f"192.0.2.{index % 3 + 1}", path="/phpinfo.php",
                      method="GET", status_code=404, event_time=start + timedelta(seconds=index))
        db_session.add(event)
        events.append(event)
    db_session.add(Event(event_type="access.error", ip="192.0.2.9", path="/phpinfo.php", method="POST",
                         status_code=404, event_time=start + timedelta(seconds=30)))
    db_session.flush()
    apply_declarative_insight_rules(db_session, events[0])
    insight = db_session.query(Insight).one()
    assert insight.evidence["event_count"] == 30
    assert insight.evidence["distinct_ip_count"] == 3
    assert insight.evidence["sample_truncated"] is True
    assert insight.related_event_ids == sorted(event.id for event in events[:20])


@pytest.mark.parametrize("fresh", [False, True])
def test_schema_two_migration_preserves_rows_and_downgrades(tmp_path, monkeypatch, fresh):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table, create_engine, inspect, select
    from app.core import settings as settings_module

    url = f"sqlite:///{tmp_path / 'schema-two.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings_module.settings, "database_url", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(url)
    if not fresh:
        command.upgrade(config, "a8b9c0d1e2f3")
        table = Table("insight_rules", MetaData(), autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(table.insert().values(id=42, rule_id="legacy", title="Legacy",
                event_types=["access.error"], path_contains_any=["/legacy"], created_at=datetime(2020, 1, 1),
                updated_at=datetime(2020, 1, 1), last_seen_at=datetime(2020, 1, 1)))
    command.upgrade(config, "head")
    table = Table("insight_rules", MetaData(), autoload_with=engine)
    if not fresh:
        with engine.connect() as connection:
            row = connection.execute(select(table)).one()
            assert row.id == 42 and row.title == "Legacy"
            assert row.kind == "web_probe" and row.match_options is None
    command.check(config)
    command.downgrade(config, "a8b9c0d1e2f3")
    assert not {"kind", "match_options"} & {column["name"] for column in inspect(engine).get_columns("insight_rules")}
    if not fresh:
        table = Table("insight_rules", MetaData(), autoload_with=engine)
        with engine.connect() as connection:
            assert connection.execute(select(table.c.id)).scalar_one() == 42
    engine.dispose()


@pytest.mark.parametrize("options", [
    {"path_equals_any": ["/x"] * 50, "status_codes": list(range(100, 200)), "methods": ["get"] * 10},
    {"path_prefix_any": ["x" * 200], "status_codes": [100, 599], "methods": ["TRACE", "CONNECT"]},
])
def test_schema_two_accepts_option_boundaries(options):
    rule = parse_rules(payload(match_options=options))[0]
    assert rule.match_options is not None
    assert all(method == method.upper() for method in rule.match_options["methods"])


@pytest.mark.parametrize("assets,countries,seconds", [(2, 1, 1), (100, 100, 300)])
def test_chain_accepts_boundaries(assets, countries, seconds):
    assert parse_rules(payload(kind="scan_ban_chain", group_by="path", min_distinct_ips=2, window_minutes=60,
        match_options={"path_equals_any": ["/x"], "min_distinct_assets": assets,
                       "min_distinct_countries": countries, "followup_seconds": seconds}))
