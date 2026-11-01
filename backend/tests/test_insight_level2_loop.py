import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest

from sqlalchemy.orm import Session

from app.models.core import Diagnostic, Insight
from app.models.events import Event
from app.models.settings import Setting
from app.plugins import manager as manager_module
from app.plugins.manager import PluginManager
from app.services import insight_correlation as correlation
from test_insight_level2 import NOW, RULE_ID, chain_db, findings, wave


def test_201_anchors_resume_after_new_session_and_keep_cycle_cutoff(chain_db):
    _, ban = wave(chain_db)
    for _ in range(200):
        chain_db.add(Event(event_type="security.ban", ip=ban.ip, event_time=ban.event_time, created_at=NOW))
    chain_db.commit()
    first = correlation.run_level2_tick(chain_db, now=NOW)
    assert first["processed"] == 100 and not first["completed"]
    saved = chain_db.query(Setting).filter(Setting.key == correlation.CYCLE_KEY).one().value
    with Session(chain_db.get_bind()) as restarted:
        second = correlation.run_level2_tick(restarted, now=NOW + timedelta(minutes=20))
        assert second["processed"] == 100 and not second["completed"]
        state = json.loads(restarted.query(Setting).filter(Setting.key == correlation.CYCLE_KEY).one().value)
        assert state["created_to"] == json.loads(saved)["created_to"]
        third = correlation.run_level2_tick(restarted, now=NOW + timedelta(minutes=21))
        assert third["processed"] == 1 and third["completed"]
        assert len(findings(restarted)) == 1


def test_database_error_isolated_per_anchor_and_cursor_advances(chain_db, monkeypatch):
    _, ban = wave(chain_db)
    chain_db.add(Event(event_type="security.ban", ip=ban.ip, event_time=ban.event_time, created_at=NOW))
    chain_db.commit()
    original = correlation.evaluate_chain
    calls = []

    def fail_first(db, rule, anchor):
        calls.append(anchor.id)
        if len(calls) == 1:
            db.add(Setting(key="plugin.traefik_log.enabled", value="duplicate"))
            db.flush()
        return original(db, rule, anchor)

    monkeypatch.setattr(correlation, "evaluate_chain", fail_first)
    result = correlation.run_level2_tick(chain_db, now=NOW)
    assert result["errors"] == 1 and result["processed"] == 2 and result["completed"]
    assert len(findings(chain_db)) == 1
    assert chain_db.query(Diagnostic).filter(Diagnostic.component == "level2").one().status == "warning"
    from app.services.events import store_event

    event = store_event(chain_db, event_type="access.error", ip="192.0.2.9", event_time=NOW)
    chain_db.commit()
    assert chain_db.get(Event, event.id) is not None


@pytest.mark.parametrize("state", ["{}", "not json", '{"last_id": true}',
    '{"started_at":"2026-11-01T12:00:00","created_from":"x","created_to":"x","last_id":-1}'])
def test_invalid_cursor_recovers_with_bounded_new_cycle(chain_db, state):
    wave(chain_db)
    chain_db.add(Setting(key=correlation.CYCLE_KEY, value=state))
    chain_db.commit()
    result = correlation.run_level2_tick(chain_db, now=NOW)
    assert result["processed"] == 1 and len(findings(chain_db)) == 1


def test_loop_uses_worker_session_and_60_second_interval(monkeypatch):
    calls = []

    async def thread(work, *args):
        calls.append((work, args))
        return {}

    async def sleep(seconds):
        assert seconds == 60
        raise asyncio.CancelledError

    monkeypatch.setattr(manager_module.asyncio, "to_thread", thread)
    monkeypatch.setattr(manager_module.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(PluginManager(Path("plugins"))._insight_level2_loop())
    assert calls == [(manager_module.run_with_session, (correlation.run_level2_tick,))]


def test_plugin_owner_disablement_is_checked_each_tick(chain_db):
    from app.models.core import InsightRule
    from app.services.insight_rules import invalidate_active_rules_cache

    rule = chain_db.query(InsightRule).filter_by(rule_id=RULE_ID).one()
    rule.source = "plugin:geoip"
    chain_db.add(Setting(key="plugin.geoip.enabled", value="false"))
    wave(chain_db)
    invalidate_active_rules_cache(chain_db)
    correlation.run_level2_tick(chain_db, now=NOW)
    assert findings(chain_db) == []
    chain_db.query(Setting).filter_by(key="plugin.geoip.enabled").one().value = "true"
    chain_db.commit()
    correlation.run_level2_tick(chain_db, now=NOW)
    assert len(findings(chain_db)) == 1


def test_rule_error_does_not_block_other_rules(chain_db):
    from app.models.core import InsightRule
    from app.services.insight_rules import import_ruleset

    data = json.loads((Path(__file__).parent / "fixtures" / "insight-level2.json").read_text())
    data["rules"][0]["id"] = "aaa.broken"
    import_ruleset(chain_db, data, source="remote")
    rule = chain_db.query(InsightRule).filter_by(rule_id="aaa.broken").one()
    rule.match_options = {}
    wave(chain_db)
    result = correlation.run_level2_tick(chain_db, now=NOW)
    assert result["errors"] == 1 and len(findings(chain_db)) == 1


def test_twenty_chain_rules_and_sixty_minute_window(chain_db):
    from app.services.insight_rules import import_ruleset

    data = json.loads((Path(__file__).parent / "fixtures" / "insight-level2.json").read_text())
    data["rules"] = [{**data["rules"][0], "id": f"test.chain.{index}", "window_minutes": 60} for index in range(20)]
    import_ruleset(chain_db, data, source="bundled")
    probes, ban = wave(chain_db)
    probes[0].event_time = NOW - timedelta(minutes=60, seconds=1)
    chain_db.commit()
    result = correlation.run_level2_tick(chain_db, now=NOW)
    assert result["errors"] == 0
    assert chain_db.query(Insight).filter(Insight.type.like("test.chain.%")).count() == 20


def test_failed_batch_commit_does_not_advance_cursor(chain_db, monkeypatch):
    _, ban = wave(chain_db)
    for _ in range(100):
        chain_db.add(Event(event_type="security.ban", ip=ban.ip, event_time=NOW, created_at=NOW))
    chain_db.commit()

    def fail_commit():
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(chain_db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            correlation.run_level2_tick(chain_db, now=NOW)
    chain_db.rollback()
    assert chain_db.query(Setting).filter_by(key=correlation.CYCLE_KEY).first() is None
    assert correlation.run_level2_tick(chain_db, now=NOW)["processed"] == 100


def test_raw_event_queries_are_bounded_and_use_existing_indexes(chain_db):
    from sqlalchemy import event

    wave(chain_db, count=40)
    queries = []
    plans = []
    bind = chain_db.get_bind()

    def inspect_query(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM events" in statement:
            queries.append(statement)
            plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
            plans.append(" ".join(str(row[-1]) for row in plan))

    event.listen(bind, "before_cursor_execute", inspect_query)
    try:
        correlation.run_level2_tick(chain_db, now=NOW)
    finally:
        event.remove(bind, "before_cursor_execute", inspect_query)
    assert len(queries) == 4  # anchors, latest request, aggregate, examples
    for statement, plan in zip(queries, plans):
        assert "events.created_at >=" in statement or "events.event_time >=" in statement
        assert "LIMIT" in statement or "count(events.id)" in statement
        assert "USING INDEX" in plan or "USING COVERING INDEX" in plan
        assert "SCAN events" not in plan


def test_combined_sources_cannot_exceed_twenty_rules_per_tick(chain_db):
    from app.services.insight_rules import import_ruleset

    data = json.loads((Path(__file__).parent / "fixtures" / "insight-level2.json").read_text())
    data["rules"] = [{**data["rules"][0], "id": f"a.chain.{index:02}"} for index in range(20)]
    import_ruleset(chain_db, data, source="remote")
    wave(chain_db)
    correlation.run_level2_tick(chain_db, now=NOW)
    assert chain_db.query(Insight).count() == 20
    assert findings(chain_db) == []


def test_shutdown_cancels_chain_trend_and_notification_loops(monkeypatch):
    async def thread(work, *args):
        return {}

    monkeypatch.setattr(manager_module.asyncio, "to_thread", thread)

    async def run():
        manager = PluginManager(Path("plugins"))
        manager.tasks = [asyncio.create_task(loop()) for loop in (
            manager._insight_level2_loop, manager._insight_trends_loop,
            manager._notification_dispatch_loop)]
        tasks = list(manager.tasks)
        await asyncio.sleep(0)
        await manager.shutdown()
        assert all(task.cancelled() for task in tasks)
        assert manager.tasks == []

    asyncio.run(run())
