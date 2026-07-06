from pathlib import Path

import pytest

from app.core import plugin_registry
from app.plugins.loader import is_plugin_env_disabled
from app.plugins.manager import PluginManager, get_plugin_manager

PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


@pytest.fixture(autouse=True)
def _restore_global_registry():
    # discover() on any manager repopulates the process-global plugin_registry
    # and i18n locales. These tests build throwaway managers with env vars set,
    # so restore the real, complete registry afterwards for other test modules.
    yield
    get_plugin_manager().discover()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_env_disable_truthy_values(monkeypatch, value):
    monkeypatch.setenv("OSD_PLUGIN_CROWDSEC_DISABLED", value)
    assert is_plugin_env_disabled("crowdsec") is True


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "disabled"])
def test_env_disable_falsy_values(monkeypatch, value):
    monkeypatch.setenv("OSD_PLUGIN_CROWDSEC_DISABLED", value)
    assert is_plugin_env_disabled("crowdsec") is False


def test_env_disable_unset(monkeypatch):
    monkeypatch.delenv("OSD_PLUGIN_CROWDSEC_DISABLED", raising=False)
    assert is_plugin_env_disabled("crowdsec") is False


def test_env_disable_matches_any_name(monkeypatch):
    # Either the directory name or the id may switch a plugin off.
    monkeypatch.setenv("OSD_PLUGIN_MQTT_HASS_DISABLED", "true")
    assert is_plugin_env_disabled("mqtt", "mqtt-hass") is True
    assert is_plugin_env_disabled("mqtt") is False


def test_mqtt_disabled_by_directory_name(monkeypatch):
    monkeypatch.setenv("OSD_PLUGIN_MQTT_DISABLED", "true")
    manager = PluginManager(PLUGINS_DIR)
    manager.discover()
    assert "mqtt-hass" not in manager.plugins
    assert "crowdsec" in manager.plugins  # unrelated plugins unaffected


def test_mqtt_disabled_by_plugin_id(monkeypatch):
    # Directory is "mqtt" but the plugin id is "mqtt-hass"; the id spelling
    # must also disable it (checked after the module is loaded).
    monkeypatch.setenv("OSD_PLUGIN_MQTT_HASS_DISABLED", "true")
    manager = PluginManager(PLUGINS_DIR)
    manager.discover()
    assert "mqtt-hass" not in manager.plugins


def test_all_plugins_present_without_env(monkeypatch):
    for var in ("OSD_PLUGIN_MQTT_DISABLED", "OSD_PLUGIN_MQTT_HASS_DISABLED", "OSD_PLUGIN_CROWDSEC_DISABLED"):
        monkeypatch.delenv(var, raising=False)
    manager = PluginManager(PLUGINS_DIR)
    manager.discover()
    assert "mqtt-hass" in manager.plugins
    assert "crowdsec" in manager.plugins


def test_registry_excludes_env_disabled_plugin(monkeypatch):
    monkeypatch.setenv("OSD_PLUGIN_CROWDSEC_DISABLED", "true")
    manager = PluginManager(PLUGINS_DIR)
    manager.discover()  # discovery publishes the global registry

    assert "crowdsec" not in plugin_registry.plugin_ids()
    # crowdsec is a datasource, so the feature-flag capability list drops it
    # too - an env-disabled plugin never counts as active even if its DB
    # setting still says enabled.
    assert "crowdsec" not in plugin_registry.ids_with_capability("datasource")
    assert "traefik_log" in plugin_registry.ids_with_capability("datasource")
