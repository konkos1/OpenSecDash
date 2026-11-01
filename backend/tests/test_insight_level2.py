import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.models.core import Insight
from app.models.events import Event
from app.models.settings import Setting
from app.services.insight_rules import import_ruleset
from app.services.insight_correlation import run_level2_tick
from app.services.events import store_event
from conftest import import_plugin_module

NOW = datetime(2026, 11, 1, 12)
RULE_ID = "web.distributed_scan_then_ban"


@pytest.fixture()
def chain_db(db_session):
    data = json.loads((Path(__file__).parent / "fixtures" / "insight-level2.json").read_text())
    import_ruleset(db_session, data, source="bundled")
    for plugin in ("traefik_log", "crowdsec"):
        db_session.add(Setting(key=f"plugin.{plugin}.enabled", value="true"))
    db_session.commit()
    return db_session


def wave(db, *, count=20, ban_ip="192.0.2.2", ban_time=NOW, **ban_values):
    probes = []
    for index in range(count):
        event = Event(event_type="access.error", ip=f"192.0.2.{index % 2 + 1}",
            asset_id=index % 2 + 1, country=("DE", "FR")[index % 2], path="/wp-login.php",
            status_code=404, method="GET", event_time=NOW - timedelta(seconds=count - index), created_at=NOW)
        db.add(event)
        probes.append(event)
    values = dict(event_type="security.ban", ip=ban_ip, event_time=ban_time, created_at=NOW)
    db.add(ban := Event(**{**values, **ban_values}))
    db.commit()
    return probes, ban


def findings(db):
    return db.query(Insight).filter(Insight.type == RULE_ID).all()


def test_positive_snapshot_and_repeat_are_stable(chain_db):
    probes, ban = wave(chain_db, count=30)
    run_level2_tick(chain_db, now=NOW)
    insight, = findings(chain_db)
    assert insight.ip == ban.ip and insight.asset_id is None
    assert insight.occurred_at == ban.event_time and insight.confidence <= 0.8
    assert insight.correlation_key == "rule" and insight.dedupe_key == f"{RULE_ID}:ban:{ban.id}"
    eligible = [p for p in probes if p.event_time <= NOW]
    assert insight.related_event_ids == sorted([ban.id] + [p.id for p in eligible[-19:]])
    assert insight.evidence == {
        "version": 1, "kind": "correlation", "rule_id": RULE_ID, "rule_source": "bundled",
        "ruleset_version": "2026-11-01", "window_start": "2026-11-01T11:54:59+00:00",
        "window_end": "2026-11-01T11:59:59+00:00", "event_count": 30,
        "distinct_ip_count": 2, "distinct_asset_count": 2, "distinct_country_count": 2,
        "followup_seconds": 60, "followup_gap_seconds": 1, "sample_count": 20, "sample_truncated": True,
    }
    original = (insight.description, insight.evidence, insight.related_event_ids)
    probes[0].country = "US"
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW + timedelta(seconds=60))
    assert len(findings(chain_db)) == 1
    assert (insight.description, insight.evidence, insight.related_event_ids) == original
    assert "does not prove" in insight.description


@pytest.mark.parametrize("scenario,event_type", [
    ("opensecdash/manual-permanent-asn-ban/AS64500", "security.ban.asn_policy"),
    ("manual-permanent-asn-ban/AS64500", "security.ban.asn_policy"),
    ("crowdsecurity/http-probing", "security.ban"),
])
@pytest.mark.parametrize("quoted", [False, True])
def test_imported_policy_bans_do_not_anchor_scan_chains(chain_db, scenario, event_type, quoted):
    _, placeholder = wave(chain_db)
    chain_db.delete(placeholder)
    chain_db.commit()
    plugin = import_plugin_module("crowdsec", "plugin").Plugin()
    message = f'(machine/opensecdash) {scenario} by ip 192.0.2.2 : 7d ban on Ip 192.0.2.2'
    line = f'time="2026-11-01T12:00:00Z" level=info msg="{message}"'
    if quoted:
        line = (f'time="2026-11-01T12:00:00Z" level=info scenario="{scenario}" '
            'msg="7d ban on Ip 192.0.2.2"')
    parsed = plugin.parse_log_line(line)
    assert parsed is not None
    ban = store_event(chain_db, **parsed, created_at=NOW)
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)

    assert ban.event_type == event_type
    assert ban.raw_data == line
    assert ban.data_json is not None
    assert ban.data_json["scenario"] == scenario
    if event_type == "security.ban.asn_policy":
        assert ban.data_json["asn"] == "AS64500"
        assert findings(chain_db) == []
        assert chain_db.query(Insight).filter_by(type="asn_policy_security_ban").one().related_event_ids == [ban.id]
        assert chain_db.query(Insight).filter_by(type="security_ban_observed").count() == 0
    else:
        insight, = findings(chain_db)
        assert ban.id in insight.related_event_ids


@pytest.mark.parametrize("existing_type", ["security.ban.asn_policy", "security.ban"])
def test_imported_policy_ban_only_merges_with_policy_event(chain_db, existing_type):
    existing = store_event(chain_db, plugin="crowdsec", event_type=existing_type,
        ip="192.0.2.2", event_time=NOW, created_at=NOW,
        data_json={"action_id": 42, "asn": "AS64500", "provider_name": "Example"})
    plugin = import_plugin_module("crowdsec", "plugin").Plugin()
    parsed = plugin.parse_log_line('time="2026-11-01T12:00:00Z" level=info '
        'msg="(machine/opensecdash) opensecdash/manual-permanent-asn-ban/AS64500 '
        'by ip 192.0.2.2 : 7d ban on Ip 192.0.2.2"')
    assert parsed is not None
    imported = store_event(chain_db, **parsed, created_at=NOW)
    chain_db.commit()

    assert (imported.id == existing.id) == (existing_type == "security.ban.asn_policy")
    assert imported.event_type == "security.ban.asn_policy"
    assert chain_db.query(Event).count() == (1 if existing_type == "security.ban.asn_policy" else 2)
    assert imported.data_json is not None
    assert imported.data_json["scenario"] == "opensecdash/manual-permanent-asn-ban/AS64500"
    assert existing.data_json is not None
    assert existing.data_json["provider_name"] == "Example"
    assert chain_db.query(Insight).filter_by(type="asn_policy_security_ban").count() == 1


@pytest.mark.parametrize("field,value", [("ip", "192.0.2.1"), ("asset_id", 1),
    ("asset_id", None), ("asset_id", 0), ("country", "DE"), ("country", None),
    ("country", ""), ("country", "unknown"), ("country", "XX"), ("country", "ZZ"), ("country", "--"), ("country", "12")])
def test_each_distinct_threshold_and_unknown_values(chain_db, field, value):
    probes, _ = wave(chain_db)
    for probe in probes:
        setattr(probe, field, value)
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db) == []


@pytest.mark.parametrize("options", [
    {"count": 19}, {"ban_ip": "192.0.2.99"}, {"ban_ip": None},
    {"ban_time": NOW - timedelta(minutes=6)}, {"ban_time": NOW + timedelta(seconds=61)},
    {"event_type": "security.ban.manual"}, {"event_type": "security.ban.asn_policy"},
    {"data_json": {"manual": True}}, {"data_json": {"simulated": True}},
    {"data_json": {"result": "dry-run: action was recorded but not executed"}},
])
def test_invalid_ban_or_insufficient_events(chain_db, options):
    wave(chain_db, **options)
    run_level2_tick(chain_db, now=NOW + timedelta(minutes=2))
    assert findings(chain_db) == []


def test_late_probes_and_geoip_are_rechecked_but_old_bans_expire(chain_db):
    probes, ban = wave(chain_db)
    for probe in probes:
        probe.country = None
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db) == []
    for index, probe in enumerate(probes):
        probe.country = ("DE", "FR")[index % 2]
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW + timedelta(minutes=14))
    assert len(findings(chain_db)) == 1
    run_level2_tick(chain_db, now=NOW + timedelta(minutes=16))
    assert len(findings(chain_db)) == 1
    chain_db.query(Insight).delete()
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW + timedelta(minutes=30))
    assert findings(chain_db) == []


@pytest.mark.parametrize("plugin", ["traefik_log", "crowdsec"])
def test_disabled_input_prevents_findings(chain_db, plugin):
    wave(chain_db)
    chain_db.query(Setting).filter(Setting.key == f"plugin.{plugin}.enabled").one().value = "false"
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db) == []


def test_ban_imported_before_probes_is_revisited(chain_db):
    probes, ban = wave(chain_db)
    # Remove only raw requests: the anchor already exists during the first cycle.
    saved = [{"event_type": p.event_type, "ip": p.ip, "asset_id": p.asset_id, "country": p.country,
        "path": p.path, "event_time": p.event_time, "created_at": NOW + timedelta(minutes=1)} for p in probes]
    for probe in probes:
        chain_db.delete(probe)
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db) == []
    chain_db.add_all([Event(**values) for values in saved])
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW + timedelta(minutes=1))
    assert len(findings(chain_db)) == 1


@pytest.mark.parametrize("gap,expected", [(0, True), (60, True), (61, False)])
def test_closed_followup_window(chain_db, gap, expected):
    wave(chain_db, ban_time=NOW - timedelta(seconds=1) + timedelta(seconds=gap))
    run_level2_tick(chain_db, now=NOW)
    assert bool(findings(chain_db)) is expected


@pytest.mark.parametrize("offset,expected", [(300, True), (301, False)])
def test_closed_probe_window_and_later_requests_do_not_count(chain_db, offset, expected):
    probes, ban = wave(chain_db)
    probes[0].event_time = NOW - timedelta(seconds=1 + offset)
    chain_db.add(Event(event_type="access.error", ip="192.0.2.9", asset_id=3, country="US",
        path="/wp-login.php", event_time=NOW + timedelta(seconds=1), created_at=NOW))
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    assert bool(findings(chain_db)) is expected
    if expected:
        assert findings(chain_db)[0].evidence["event_count"] == 20


def test_only_probes_before_ban_can_satisfy_threshold(chain_db):
    wave(chain_db, ban_time=NOW - timedelta(seconds=2))
    run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db) == []


def test_sample_uses_event_time_then_id_and_includes_ban(chain_db):
    probes, ban = wave(chain_db, count=40)
    # A last-imported row with an old event time must not displace newer examples.
    chain_db.add(old := Event(event_type="access.error", ip="192.0.2.9", asset_id=3, country="US",
        path="/xmlrpc.php", event_time=NOW - timedelta(minutes=4), created_at=NOW))
    probes[-3].event_time = probes[-1].event_time
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    insight, = findings(chain_db)
    assert old.id not in insight.related_event_ids
    assert insight.related_event_ids == sorted([ban.id] + [p.id for p in probes[-19:]])
    assert insight.evidence["event_count"] == 41 and len(insight.related_event_ids) == 20


@pytest.mark.parametrize("field,value", [("status_code", 200), ("method", "POST"),
    ("method", None), ("path", "/wp-login.php/child"), ("event_type", "access.allowed")])
def test_chain_applies_all_schema_two_filters(chain_db, field, value):
    from app.models.core import InsightRule
    from app.services.insight_rules import invalidate_active_rules_cache

    rule = chain_db.query(InsightRule).filter_by(rule_id=RULE_ID).one()
    rule.path_contains_any = []
    rule.match_options = {**rule.match_options, "path_equals_any": ["/WP-LOGIN.PHP"],
        "status_codes": [404], "methods": ["GET"]}
    probes, ban = wave(chain_db)
    setattr(probes[0], field, value)
    chain_db.commit()
    invalidate_active_rules_cache(chain_db)
    run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db) == []


def test_cooldown_suppresses_other_anchors_without_adding_them_to_evidence(chain_db):
    probes, ban = wave(chain_db)
    chain_db.add(other := Event(event_type="security.ban", ip=ban.ip, event_time=NOW + timedelta(seconds=10), created_at=NOW))
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    insight, = findings(chain_db)
    assert ban.id in insight.related_event_ids and other.id not in insight.related_event_ids
    assert insight.dedupe_key == f"{RULE_ID}:ban:{ban.id}"


def test_one_opt_in_notification_across_rechecks_and_geoip_changes(chain_db, monkeypatch):
    from app.models.core import Notification, NotificationRule
    from app.services import notifications
    from app.services.notifications import insight_notification_rule_id

    monkeypatch.setattr(notifications, "utc_now", lambda: NOW)
    rule = chain_db.query(NotificationRule).filter_by(rule_id=insight_notification_rule_id(RULE_ID)).one()
    assert rule.enabled is False
    rule.enabled = True
    chain_db.add_all([Setting(key=key, value=value) for key, value in {
        "notifications.enabled": "true", "notifications.smtp_host": "smtp.example.test",
        "notifications.smtp_sender": "sender@example.test", "notifications.smtp_recipient": "admin@example.test",
    }.items()])
    probes, _ = wave(chain_db)
    notifications.invalidate_rules_cache()
    try:
        run_level2_tick(chain_db, now=NOW)
        assert chain_db.query(Notification).filter_by(rule_id=rule.rule_id).count() == 1
        probes[0].country = "US"
        chain_db.commit()
        run_level2_tick(chain_db, now=NOW + timedelta(minutes=1))
        assert chain_db.query(Notification).filter_by(rule_id=rule.rule_id).count() == 1
    finally:
        notifications.invalidate_rules_cache()


def test_newly_imported_historical_ban_is_evaluated_by_creation_time(chain_db):
    probes, ban = wave(chain_db)
    for item in [*probes, ban]:
        item.event_time -= timedelta(days=365)
    chain_db.commit()
    run_level2_tick(chain_db, now=NOW)
    insight, = findings(chain_db)
    assert insight.occurred_at == ban.event_time
    assert insight.evidence["event_count"] == 20


def test_confidence_is_capped_for_imported_rules(chain_db):
    from app.models.core import InsightRule
    from app.services.insight_rules import invalidate_active_rules_cache

    chain_db.query(InsightRule).filter_by(rule_id=RULE_ID).one().confidence = 1.0
    wave(chain_db)
    invalidate_active_rules_cache(chain_db)
    run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db)[0].confidence == 0.8
