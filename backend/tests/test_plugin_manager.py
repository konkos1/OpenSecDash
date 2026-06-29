from pathlib import Path

from app.models.core import Datasource, Diagnostic, PluginRecord
from app.models.settings import Setting
from app.plugins.base import DatasourcePlugin, PluginMetadata, PluginSetting
from app.plugins.manager import PluginManager


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
