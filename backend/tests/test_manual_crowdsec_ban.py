from datetime import datetime, timedelta

from app.models.events import Event
from app.models.settings import Setting
from app.services.actions import create_action
from app.services.events import store_event


def test_manual_ban_action_records_scenario_and_duration(db_session):
    # Dry-run (the default) is enough to exercise the store_event() call at
    # the end of execute_action() without needing a real CrowdSec/plugin
    # manager - the CrowdSec page reads data_json.scenario/duration for every
    # ban row, manual or log-imported, and both used to be missing here.
    action = create_action(db_session, "security.ban", "8.8.8.8", "ip", {"duration": "12h", "reason": "Manual ban via OpenSecDash"}, confirmed=True)
    db_session.commit()

    event = db_session.query(Event).filter_by(event_type="security.ban.manual", ip="8.8.8.8").one()

    # The action id is embedded into the reason actually sent to CrowdSec (and
    # thus into the stored scenario too) so a later log-tailed re-import of
    # CrowdSec's own log line can be correlated back to this exact action.
    assert event.data_json["scenario"] == f"Manual ban via OpenSecDash (action #{action.id})"
    assert event.data_json["duration"] == "12h"
    assert event.data_json["manual"] is True
    assert event.data_json["action_id"] == action.id
    assert action.status == "completed"


def test_manual_ban_gets_default_reason_without_reason_parameter(db_session):
    # The plugin supplies the same default reason used by the IP action form,
    # so the id-embedding step can correlate this action with a later log line.
    action = create_action(db_session, "security.ban", "9.9.9.9", "ip", {"duration": "4h"}, confirmed=True)
    db_session.commit()

    event = db_session.query(Event).filter_by(event_type="security.ban.manual", ip="9.9.9.9").one()

    assert event.data_json["scenario"] == f"Manual ban via OpenSecDash (action #{action.id})"
    assert event.data_json["duration"] == "4h"
    assert action.id is not None


def test_manual_unban_does_not_record_ban_scenario_fields(db_session):
    create_action(db_session, "security.unban", "1.2.3.4", "ip", {"decision_id": "42"}, confirmed=True)
    db_session.commit()

    event = db_session.query(Event).filter_by(event_type="security.unban.manual", ip="1.2.3.4").one()

    assert "scenario" not in event.data_json
    assert "duration" not in event.data_json


def test_log_tailed_ban_within_window_merges_into_manual_event_instead_of_duplicating(db_session):
    # Simulates the real sequence: the manual ban is recorded immediately
    # (no raw_data, synthetic scenario/duration), then a few seconds later
    # the crowdsec_log datasource plugin tails CrowdSec's own log line about
    # the same decision (different event_type, has raw_data, its own
    # scenario/duration reading). These must merge into a single event.
    manual_time = datetime(2026, 7, 6, 12, 0, 0)
    manual = store_event(
        db_session,
        source="Action Framework",
        source_id="actions",
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban.manual",
        severity="info",
        ip="203.0.113.5",
        event_time=manual_time,
        data_json={"action_id": 1, "manual": True, "trigger": "manual", "scenario": "Manual ban via OpenSecDash", "duration": "4h"},
    )
    db_session.commit()

    log_tailed = store_event(
        db_session,
        source="CrowdSec Log",
        source_id="crowdsec-log",
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban",
        severity="warning",
        ip="203.0.113.5",
        event_time=manual_time + timedelta(seconds=8),
        data_json={"scenario": None, "duration": "4h", "message": "raw log line"},
        raw_data='time="2026-07-06T12:00:08Z" ban on Ip 203.0.113.5',
    )
    db_session.commit()

    assert log_tailed.id == manual.id
    assert getattr(log_tailed, "_opensecdash_created") is False
    assert db_session.query(Event).filter_by(ip="203.0.113.5", plugin="crowdsec").count() == 1

    retained = db_session.query(Event).filter_by(ip="203.0.113.5", plugin="crowdsec").one()
    assert retained.event_type == "security.ban.manual"  # the original manual event, not overwritten
    assert retained.data_json["scenario"] == "Manual ban via OpenSecDash"  # kept, not clobbered by None
    assert retained.data_json["duration"] == "4h"


def test_log_tailed_ban_fills_in_missing_duration_without_overwriting_scenario(db_session):
    manual_time = datetime(2026, 7, 6, 13, 0, 0)
    store_event(
        db_session,
        source="Action Framework",
        source_id="actions",
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban.manual",
        severity="info",
        ip="203.0.113.9",
        event_time=manual_time,
        data_json={"manual": True, "scenario": "Manual ban via OpenSecDash"},  # no duration recorded
    )
    db_session.commit()

    store_event(
        db_session,
        source="CrowdSec Log",
        source_id="crowdsec-log",
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban",
        severity="warning",
        ip="203.0.113.9",
        event_time=manual_time + timedelta(seconds=5),
        data_json={"duration": "6h"},
        raw_data="some other log line",
    )
    db_session.commit()

    retained = db_session.query(Event).filter_by(ip="203.0.113.9", plugin="crowdsec").one()
    assert retained.data_json["scenario"] == "Manual ban via OpenSecDash"
    assert retained.data_json["duration"] == "6h"


def test_log_line_with_action_id_correlates_regardless_of_delay(db_session):
    # Confirmed against a real CrowdSec instance: LAPI-created decisions
    # echo the given reason verbatim into crowdsec.log, e.g.
    #   msg="(<machine>/opensecdash) Manual ban via OpenSecDash (action #42) by ip X : 1m ban on Ip X"
    # Correlating on this id must work even far outside the time-window
    # fallback, since it doesn't depend on timing at all.
    action = create_action(db_session, "security.ban", "45.33.32.156", "ip", {"duration": "4h", "reason": "Manual ban via OpenSecDash"}, confirmed=True)
    db_session.commit()
    manual = db_session.query(Event).filter_by(event_type="security.ban.manual", ip="45.33.32.156").one()

    log_tailed = store_event(
        db_session,
        source="CrowdSec Log",
        source_id="crowdsec-log",
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban",
        severity="warning",
        ip="45.33.32.156",
        event_time=manual.event_time + timedelta(minutes=10),  # well outside the 30s fallback window
        data_json={"duration": "4h", "message": "raw log line"},
        raw_data=(
            f'time="2026-07-06T16:49:14+02:00" level=info msg="(machine/opensecdash) '
            f"Manual ban via OpenSecDash (action #{action.id}) by ip 45.33.32.156 : 4h ban on Ip 45.33.32.156\" module=db"
        ),
    )
    db_session.commit()

    assert log_tailed.id == manual.id
    assert db_session.query(Event).filter_by(ip="45.33.32.156", plugin="crowdsec").count() == 1


def test_ban_unban_reban_within_window_keeps_both_bans_as_distinct_events(db_session):
    # The scenario a pure time-window match would get wrong: ban, unban, then
    # a fresh re-ban of the SAME ip, all within the fallback window. Each
    # action has its own id embedded in its reason, so each log-tailed line
    # correlates to the correct action instead of both collapsing into the
    # first (by-then-stale) ban.
    from app.models.core import CrowdSecDecision
    from app.core.time import utc_now

    ip = "45.33.32.157"
    first_ban = create_action(db_session, "security.ban", ip, "ip", {"duration": "4h", "reason": "Manual ban via OpenSecDash"}, confirmed=True)
    db_session.commit()
    first_manual = db_session.query(Event).filter_by(event_type="security.ban.manual", ip=ip).one()

    db_session.add(Setting(key="action_dry_run", value="false"))
    db_session.add(CrowdSecDecision(decision_id="900", ip=ip, decision_type="ban", synced_at=utc_now().replace(tzinfo=None)))
    db_session.commit()
    monkeypatch_execute = lambda db, action: None  # noqa: E731 - avoid touching a real plugin manager for the unban
    import app.services.actions as actions_module

    original_execute_action = actions_module.execute_action
    try:
        actions_module.execute_action = monkeypatch_execute
        create_action(db_session, "security.unban", ip, "ip", {"decision_id": "900"}, confirmed=True)
    finally:
        actions_module.execute_action = original_execute_action
    db_session.query(Setting).filter_by(key="action_dry_run").update({"value": "true"})
    db_session.query(CrowdSecDecision).filter_by(decision_id="900").delete()
    db_session.commit()

    second_ban = create_action(db_session, "security.ban", ip, "ip", {"duration": "4h", "reason": "Manual ban via OpenSecDash"}, confirmed=True)
    db_session.commit()
    second_manual = db_session.query(Event).filter_by(event_type="security.ban.manual", ip=ip).order_by(Event.id.desc()).first()
    assert second_manual is not None
    assert second_manual.id != first_manual.id

    # CrowdSec's log for the SECOND ban arrives a few seconds later, well
    # within the fallback time window of the (by now stale) first ban.
    log_tailed = store_event(
        db_session,
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban",
        ip=ip,
        event_time=second_manual.event_time + timedelta(seconds=5),
        data_json={"duration": "4h"},
        raw_data=f"(machine/opensecdash) Manual ban via OpenSecDash (action #{second_ban.id}) by ip {ip} : 4h ban on Ip {ip}",
    )
    db_session.commit()

    assert log_tailed.id == second_manual.id  # correlated to the SECOND ban, not the stale first one
    ban_events = db_session.query(Event).filter(Event.ip == ip, Event.plugin == "crowdsec", Event.event_type.like("security.ban%")).all()
    assert len(ban_events) == 2  # first ban and second ban remain distinct
    assert {e.id for e in ban_events} == {first_manual.id, second_manual.id}


def test_crowdsec_ban_outside_window_is_not_merged(db_session):
    manual_time = datetime(2026, 7, 6, 14, 0, 0)
    store_event(
        db_session,
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban.manual",
        ip="198.51.100.1",
        event_time=manual_time,
        data_json={"manual": True, "scenario": "Manual ban via OpenSecDash", "duration": "4h"},
    )
    db_session.commit()

    # A genuinely separate ban for the same IP, well outside the merge
    # window, must still create its own row.
    store_event(
        db_session,
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.ban",
        ip="198.51.100.1",
        event_time=manual_time + timedelta(hours=2),
        data_json={"scenario": "crowdsecurity/ssh-bf", "duration": "4h"},
        raw_data="unrelated later ban line",
    )
    db_session.commit()

    assert db_session.query(Event).filter_by(ip="198.51.100.1", plugin="crowdsec").count() == 2
