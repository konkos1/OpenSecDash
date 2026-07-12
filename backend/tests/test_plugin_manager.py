import asyncio
from pathlib import Path

import pytest

from app.models.core import Datasource, Diagnostic, PluginRecord
from app.models.events import Event
from app.models.settings import Setting
from app.plugins.base import DatasourcePlugin, PluginMetadata, PluginSetting
from app.core.i18n import register_extra_locales, translate
from app.core import plugin_registry
from app.plugins.manager import PluginManager, get_plugin_manager
from app.services.events import DuplicateRule, _DUPLICATE_RULES, register_duplicate_rules
import app.plugins.manager as manager_module


class ManyEventsPlugin(DatasourcePlugin):
    metadata = PluginMetadata(id="many_events", name="Many Events", capabilities=["datasource"])
    settings = [PluginSetting("enabled", "many_events.enabled", "many_events.enabled.help", type="boolean", default="true")]
    locales = {"en": {"many_events.enabled": "Enabled", "many_events.enabled.help": "Enable"}, "de": {}}

    async def collect(self, context):
        return [
            {"source": "test", "plugin": "many_events", "event_type": "access.allowed", "raw_data": f"line-{i}"}
            for i in range(5)
        ]


class BacklogReportingPlugin(DatasourcePlugin):
    metadata = PluginMetadata(id="backlog_test", name="Backlog Test", capabilities=["datasource"])
    settings = [PluginSetting("enabled", "backlog_test.enabled", "backlog_test.enabled.help", type="boolean", default="true")]
    locales = {"en": {"backlog_test.enabled": "Enabled", "backlog_test.enabled.help": "Enable"}, "de": {}}

    async def collect(self, context):
        context.report_backlog(True, 55)
        return []


class ExampleDatasourcePlugin(DatasourcePlugin):
    metadata = PluginMetadata(
        id="example",
        name="Example Plugin",
        version="1.2.3",
        capabilities=["datasource", "action"],
    )
    settings = [
        PluginSetting("enabled", "example.enabled", "example.enabled.help", type="boolean", default="false"),
        PluginSetting("source", "example.source", "example.source.help", type="file", default="/missing/file.log"),
        PluginSetting("mode", "example.mode", "example.mode.help", type="select", default="safe", options=[("safe", "example.mode.safe")], visible_if=("enabled", "true")),
    ]
    locales = {
        "en": {
            "example.enabled": "Enabled",
            "example.enabled.help": "Enable plugin",
            "example.source": "Source",
            "example.source.help": "Input file",
            "example.mode": "Mode",
            "example.mode.help": "Mode help",
            "example.mode.safe": "Safe",
        },
        "de": {"example.enabled": "Aktiviert"},
    }


def test_asset_update_diagnostic_includes_failed_assets(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.json_assets.enabled", value="true"))
    db_session.commit()
    manager = PluginManager(Path("/not-used"))

    monkeypatch.setattr(
        manager_module,
        "refresh_asset_updates",
        lambda db: {"checked": 2, "updated": 1, "failed": 1, "failed_assets": ["Broken App (example/missing: 404 Not Found)"], "failed_reasons": ["404 Not Found"]},
    )

    manager._run_asset_update_tick(db_session, 0.0)

    diagnostic = db_session.query(Diagnostic).filter_by(plugin="asset_updates", component="plugin").one()
    assert diagnostic.status == "warning"
    assert diagnostic.last_error == "Last check: checked=2, updated=1, failed=1; failed assets: Broken App (example/missing: 404 Not Found)"


def test_asset_update_diagnostic_calls_out_global_failure(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.json_assets.enabled", value="true"))
    db_session.commit()
    manager = PluginManager(Path("/not-used"))

    monkeypatch.setattr(
        manager_module,
        "refresh_asset_updates",
        lambda db: {
            "checked": 2,
            "updated": 0,
            "failed": 2,
            "failed_assets": ["App A (owner/a: rate limited)", "App B (owner/b: rate limited)"],
            "failed_reasons": ["rate limited"],
        },
    )

    manager._run_asset_update_tick(db_session, 0.0)

    diagnostic = db_session.query(Diagnostic).filter_by(plugin="asset_updates", component="plugin").one()
    assert diagnostic.status == "warning"
    assert diagnostic.last_error == "Last check: checked=2, updated=0, failed=2; all checks failed: rate limited"


def test_discover_clears_plugin_owned_process_state(tmp_path):
    register_extra_locales({"en": {"stale.plugin.key": "Stale text"}})
    register_duplicate_rules("stale_plugin", (DuplicateRule(lambda db, values: None),))
    assert translate("stale.plugin.key") == "Stale text"
    assert "stale_plugin" in _DUPLICATE_RULES

    try:
        manager = PluginManager(tmp_path)
        manager.discover()

        assert translate("stale.plugin.key") == "stale.plugin.key"
        assert "stale_plugin" not in _DUPLICATE_RULES
        assert plugin_registry.plugin_ids() == []
    finally:
        # Restore session-global plugin discovery state for tests that run after
        # this one; production startup still discovers only once.
        get_plugin_manager().discover()


def test_plugin_manager_seeds_metadata_datasource_diagnostics_and_settings(db_session):
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"example": ExampleDatasourcePlugin()}

    manager.seed_database(db_session)

    plugin = db_session.query(PluginRecord).filter_by(id="example").one()
    assert plugin.name == "Example Plugin"
    assert plugin.version == "1.2.3"
    assert plugin.capabilities == ["datasource", "action"]
    assert db_session.query(Datasource).filter_by(plugin_id="example", status="disabled").count() == 1
    assert db_session.query(Diagnostic).filter_by(plugin="example", component="plugin", status="healthy").count() == 1
    assert db_session.query(Setting).filter_by(key="plugin.example.source", value="/missing/file.log").count() == 1


def test_plugin_settings_are_localized_and_hidden_behind_enabled_toggle(db_session):
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"example": ExampleDatasourcePlugin()}
    manager.seed_database(db_session)

    group = manager.plugin_settings(db_session, "de")[0]
    enabled, source, mode = group["settings"]

    assert enabled["label"] == "Aktiviert"
    assert source["label"] == "Source"  # falls back to English when German key is missing
    assert source["visible_if"] == {"key": "plugin.example.enabled", "value": "true"}
    assert source["error"] is None  # disabled plugin: missing source is not an error yet
    assert mode["visible_if"] == {"key": "plugin.example.enabled", "value": "true"}

    db_session.query(Setting).filter_by(key="plugin.example.enabled").one().value = "true"
    db_session.commit()
    enabled_group = manager.plugin_settings(db_session, "en")[0]
    enabled_source = next(setting for setting in enabled_group["settings"] if setting["short_key"] == "source")
    assert enabled_source["error"] == "File not found: /missing/file.log"


def test_run_datasource_tick_reports_backlog_pending_from_context(db_session):
    manager = PluginManager(Path("/not-used"))
    plugin = BacklogReportingPlugin()
    manager.plugins = {"backlog_test": plugin}
    manager.seed_database(db_session)
    db_session.commit()

    interval, backlog_pending = manager._run_datasource_tick(db_session, plugin)
    db_session.commit()

    assert backlog_pending is True
    datasource = db_session.query(Datasource).filter_by(plugin_id="backlog_test").one()
    assert datasource.backlog_pending is True
    assert datasource.backlog_progress_percent == 55


def test_disabling_datasource_persists_status_and_clears_backlog_without_extra_commit(db_session):
    manager = PluginManager(Path("/not-used"))
    plugin = BacklogReportingPlugin()
    manager.plugins = {"backlog_test": plugin}
    manager.seed_database(db_session)
    db_session.commit()

    manager._run_datasource_tick(db_session, plugin)
    db_session.commit()

    db_session.query(Setting).filter_by(key="plugin.backlog_test.enabled").one().value = "false"
    db_session.commit()

    manager._run_datasource_tick(db_session, plugin)
    # No explicit commit here: this call must commit internally (mirrors the
    # datasource loop, which closes the session right after this call with no
    # commit of its own for the "disabled" branch), or the rollback below
    # would silently discard the disabled status - as it used to before the
    # tick started committing that branch itself.
    db_session.rollback()

    datasource = db_session.query(Datasource).filter_by(plugin_id="backlog_test").one()
    assert datasource.enabled is False
    assert datasource.status == "disabled"
    assert datasource.backlog_pending is False
    assert datasource.backlog_progress_percent is None


class OneBadEventPlugin(DatasourcePlugin):
    metadata = PluginMetadata(id="one_bad_event", name="One Bad Event", capabilities=["datasource"])
    settings = [PluginSetting("enabled", "one_bad_event.enabled", "one_bad_event.enabled.help", type="boolean", default="true")]
    locales = {"en": {"one_bad_event.enabled": "Enabled", "one_bad_event.enabled.help": "Enable"}, "de": {}}

    async def collect(self, context):
        return [
            {"source": "test", "plugin": "one_bad_event", "event_type": "access.allowed", "raw_data": "good-1"},
            {"source": "test", "plugin": "one_bad_event", "event_type": "access.allowed", "raw_data": "bad", "no_such_column": True},
            {"source": "test", "plugin": "one_bad_event", "event_type": "access.allowed", "raw_data": "good-2"},
        ]


def test_run_datasource_tick_skips_malformed_events_instead_of_aborting_the_batch(db_session):
    # The plugin's file offset has already advanced past these lines when
    # storing happens, so an aborting event would lose everything after it
    # in the batch for good.
    manager = PluginManager(Path("/not-used"))
    plugin = OneBadEventPlugin()
    manager.plugins = {"one_bad_event": plugin}
    manager.seed_database(db_session)
    db_session.commit()

    manager._run_datasource_tick(db_session, plugin)

    stored = {event.raw_data for event in db_session.query(Event).filter_by(plugin="one_bad_event")}
    assert stored == {"good-1", "good-2"}


def test_next_datasource_delay_is_short_while_backlog_pending():
    assert PluginManager._next_datasource_delay(10, True) < 1
    assert PluginManager._next_datasource_delay(10, False) == 10
    assert PluginManager._next_datasource_delay(0, False) == 1


def test_cancelled_background_loop_closes_its_session_once(monkeypatch):
    class Session:
        closes = 0

        def close(self):
            self.closes += 1

    session = Session()
    manager = PluginManager(Path("/not-used"))
    monkeypatch.setattr(manager_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(manager, "_run_rollup_compaction", lambda db: 0)

    async def cancel_loop():
        task = asyncio.create_task(manager._rollup_compaction_loop())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_loop())

    assert session.closes == 1


def test_run_datasource_tick_commits_periodically_for_large_batches(db_session, monkeypatch):
    # A real first-time import can be thousands of events in one tick; this
    # locks in that all of them land even when committed in chunks rather
    # than once at the very end (see EVENTS_COMMIT_EVERY).
    monkeypatch.setattr(manager_module, "EVENTS_COMMIT_EVERY", 2)
    manager = PluginManager(Path("/not-used"))
    plugin = ManyEventsPlugin()
    manager.plugins = {"many_events": plugin}
    manager.seed_database(db_session)
    db_session.commit()

    manager._run_datasource_tick(db_session, plugin)

    assert db_session.query(Event).filter_by(plugin="many_events").count() == 5
