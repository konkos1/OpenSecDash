import asyncio
import json
from datetime import datetime

import pytest

from app.core.template_context import get_setting_value
from app.models.core import Insight
from app.plugins.base import PluginContext
from app.services.settings import save_setting
from app.services.insight_export import serialize_insight
from conftest import import_plugin_module

mqtt = import_plugin_module('mqtt', 'plugin')


def add_findings(db, count):
    rows = [Insight(type='security_ban_observed', title='Security ban observed', confidence=.95, level='high',
        timestamp=datetime(2026, 11, 1), occurred_at=datetime(2026, 11, 1),
        evidence={'version': 1, 'kind': 'direct', 'event_count': 1}) for _ in range(count)]
    db.add_all(rows)
    db.commit()
    return rows


def context(db, **settings):
    return PluginContext(db, {'enabled': 'true', 'publish_insights': 'true', 'publish_interval': 'auto', **settings})


def tick(plugin, ctx):
    asyncio.run(plugin.tick(ctx))
    ctx.db.commit()


def test_default_disabled_does_no_insight_queries_or_connections(db_session, monkeypatch):
    from sqlalchemy import event
    plugin = mqtt.Plugin()
    assert next(s.default for s in plugin.settings if s.key == 'publish_insights') == 'false'
    queries = []
    event.listen(db_session.bind, 'before_cursor_execute', lambda conn, cursor, statement, *args: queries.append(statement))
    monkeypatch.setattr(plugin, 'publish_many', lambda *args: pytest.fail('unexpected connection'))
    tick(plugin, context(db_session, publish_insights='false'))
    assert not any('FROM insights' in q for q in queries)


def test_activation_batching_restart_and_reactivation_skip_history(db_session, monkeypatch):
    plugin = mqtt.Plugin()
    ctx = context(db_session)
    add_findings(db_session, 2)
    plugin.on_settings_changed(ctx)
    db_session.commit()
    sent = []
    monkeypatch.setattr(mqtt.Plugin, 'publish_many', lambda self, ctx, messages, **kwargs: sent.extend(messages))
    rows = add_findings(db_session, 201)
    for count in (100, 200, 201):
        plugin = mqtt.Plugin()
        tick(plugin, ctx)
        assert len(sent) == count
        assert int(get_setting_value(db_session, 'plugin.mqtt-hass.insights_cursor', '0')) == rows[count - 1].id
    assert [json.loads(json.dumps(payload)) for _, payload, _ in sent] == [serialize_insight(row) for row in rows]
    assert all(topic == 'opensecdash/insights/security_ban_observed' and retain is False for topic, _, retain in sent)
    plugin.on_settings_changed(context(db_session, enabled='false'))
    db_session.commit()
    add_findings(db_session, 3)
    plugin.on_settings_changed(ctx)
    db_session.commit()
    tick(plugin, ctx)
    assert len(sent) == 201
    new = add_findings(db_session, 1)
    tick(plugin, ctx)
    assert sent[-1][1]['id'] == new[0].id


def test_broker_failure_preserves_cursor_and_retry_can_duplicate(db_session, monkeypatch):
    plugin = mqtt.Plugin()
    ctx = context(db_session)
    tick(plugin, ctx)
    rows = add_findings(db_session, 2)
    before = get_setting_value(db_session, 'plugin.mqtt-hass.insights_cursor', '0')
    sent = []
    def partial_send(ctx, messages, **kwargs):
        sent.append(messages[0])
        raise OSError('broker unavailable')
    monkeypatch.setattr(plugin, 'publish_many', partial_send)
    with pytest.raises(OSError):
        tick(plugin, ctx)
    db_session.rollback()
    assert get_setting_value(db_session, 'plugin.mqtt-hass.insights_cursor', '0') == before
    assert plugin._last_publish_error
    monkeypatch.setattr(plugin, 'publish_many', lambda ctx, messages, **kwargs: sent.extend(messages))
    tick(plugin, ctx)
    assert [payload['id'] for _, payload, _ in sent] == [rows[0].id, rows[0].id, rows[1].id]
    assert plugin._last_publish_error is None


def test_manual_interval_disables_and_reenable_skips_disabled_time(db_session, monkeypatch):
    plugin = mqtt.Plugin()
    ctx = context(db_session)
    tick(plugin, ctx)
    plugin.on_settings_changed(context(db_session, publish_interval='0'))
    db_session.commit()
    add_findings(db_session, 1)
    monkeypatch.setattr(plugin, 'publish_many', lambda *args: pytest.fail('manual mode sent insights'))
    tick(plugin, context(db_session, publish_interval='0'))
    plugin.on_settings_changed(ctx)
    db_session.commit()
    tick(plugin, ctx)


def test_invalid_cursor_fails_closed(db_session):
    plugin = mqtt.Plugin()
    save_setting(db_session, 'plugin.mqtt-hass.insights_was_active', 'true')
    save_setting(db_session, 'plugin.mqtt-hass.insights_cursor', '-1')
    db_session.commit()
    with pytest.raises(ValueError):
        tick(plugin, context(db_session))


def test_only_committed_findings_are_visible_to_worker(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database.base import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'committed.db'}")
    Base.metadata.create_all(engine)
    sent = []
    plugin = mqtt.Plugin()
    monkeypatch.setattr(plugin, 'publish_many', lambda ctx, messages, **kwargs: sent.extend(messages))
    with Session(engine, autoflush=False) as worker, Session(engine) as writer:
        tick(plugin, context(worker))
        pending = Insight(type='security_ban_observed', title='Ban')
        writer.add(pending)
        writer.flush()
        tick(plugin, context(worker))
        assert not sent
        writer.rollback()
        tick(plugin, context(worker))
        assert not sent
        committed = add_findings(writer, 1)
        tick(plugin, context(worker))
        assert [payload['id'] for _, payload, _ in sent] == [committed[0].id]
    engine.dispose()


def test_crash_after_send_before_cursor_commit_repeats_batch(db_session, monkeypatch):
    plugin = mqtt.Plugin()
    ctx = context(db_session)
    tick(plugin, ctx)
    rows = add_findings(db_session, 1)
    sent = []
    monkeypatch.setattr(plugin, 'publish_many', lambda ctx, messages, **kwargs: sent.extend(messages))
    original_commit = db_session.commit
    monkeypatch.setattr(db_session, 'commit', lambda: (_ for _ in ()).throw(RuntimeError('simulated crash')))
    with pytest.raises(RuntimeError, match='simulated crash'):
        asyncio.run(plugin.tick(ctx))
    db_session.rollback()
    monkeypatch.setattr(db_session, 'commit', original_commit)
    tick(plugin, ctx)
    assert [payload['id'] for _, payload, _ in sent] == [rows[0].id, rows[0].id]


def test_insight_transport_uses_qos_one_and_no_retain(db_session, monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(mqtt.importlib, 'import_module', lambda name: SimpleNamespace(multiple=lambda *args, **kwargs: calls.append((args, kwargs))))
    plugin = mqtt.Plugin()
    ctx = context(db_session, host='broker.example.test')
    tick(plugin, ctx)
    rows = add_findings(db_session, 1)
    tick(plugin, ctx)
    messages = calls[0][0][0]
    assert len(messages) == 1
    assert messages[0]['qos'] == 1 and messages[0]['retain'] is False
    assert json.loads(messages[0]['payload']) == serialize_insight(rows[0])


@pytest.mark.parametrize('prefix', ['bad/+', 'bad/#', 'bad\x00', ''])
def test_insight_topic_rejects_configured_wildcards(db_session, monkeypatch, prefix):
    plugin = mqtt.Plugin()
    tick(plugin, context(db_session))
    add_findings(db_session, 1)
    monkeypatch.setattr(plugin, 'publish_many', lambda *args, **kwargs: pytest.fail('unsafe topic sent'))
    with pytest.raises(ValueError, match='topic prefix'):
        tick(plugin, context(db_session, topic_prefix=prefix))


def test_existing_asset_tick_modes_and_retained_updates(db_session, monkeypatch):
    from app.models.assets import Asset
    from app.models.systems import System
    plugin = mqtt.Plugin()
    system = System(vmid='synthetic-system', hostname='example.test')
    db_session.add(system)
    db_session.flush()
    asset = Asset(system_id=system.id, name='example-app', version='1', latest_version='2', release_url='https://example.test/releases', mqtt_publish_enabled=True)
    db_session.add(asset)
    db_session.commit()
    calls = []
    monkeypatch.setattr(plugin, 'publish_many', lambda ctx, messages, **kwargs: calls.extend(messages))
    tick(plugin, context(db_session, publish_insights='false'))
    assert not calls
    tick(plugin, context(db_session, publish_insights='false', publish_interval='0'))
    assert not calls
    tick(plugin, context(db_session, publish_insights='false', publish_interval='60'))
    assert len(calls) == 2 and all(retain for _, _, retain in calls)
    calls.clear()
    asyncio.run(plugin.export_asset(context(db_session, publish_insights='false'), asset))
    assert len(calls) == 2 and all(retain for _, _, retain in calls)


def test_manager_auto_worker_publishes_without_another_thread(db_session, monkeypatch):
    from app.plugins.manager import PluginManager
    from conftest import PLUGINS_DIR
    manager = PluginManager(PLUGINS_DIR)
    plugin = mqtt.Plugin()
    manager.plugins = {'mqtt-hass': plugin}
    for key in ('enabled', 'publish_insights'):
        save_setting(db_session, 'plugin.mqtt-hass.' + key, 'true')
    manager.on_settings_changed(db_session, 'mqtt-hass')
    db_session.commit()
    rows = add_findings(db_session, 1)
    sent = []
    monkeypatch.setattr(plugin, 'publish_many', lambda ctx, messages, **kwargs: sent.extend(messages))
    monkeypatch.setattr(asyncio, 'to_thread', lambda *args, **kwargs: pytest.fail('session moved to another thread'))
    assert manager._run_periodic_tick(db_session, plugin) == 60
    assert [payload['id'] for _, payload, _ in sent] == [rows[0].id]
