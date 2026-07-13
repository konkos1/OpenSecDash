from types import SimpleNamespace
from typing import Any, cast

from app.api import pages
from app.models.core import Diagnostic, PluginRecord
from app.models.settings import Setting
from app.plugins.manager import PluginManager
from conftest import import_plugin_module
from pathlib import Path


def _set(db_session, key: str, value: str) -> None:
    existing = db_session.query(Setting).filter(Setting.key == key).first()
    if existing is None:
        db_session.add(Setting(key=key, value=value))
    else:
        existing.value = value
    db_session.commit()


def test_enabled_plugin_diagnostic_no_longer_shows_stale_disabled(monkeypatch, db_session):
    _set(db_session, "plugin.mqtt-hass.enabled", "true")
    db_session.add(PluginRecord(id="mqtt-hass", name="MQTT", capabilities=["export"]))
    db_session.add(Diagnostic(plugin="mqtt-hass", component="plugin", status="disabled", last_error="Plugin is disabled and not running."))
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.diagnostics_page(cast(Any, SimpleNamespace(url=SimpleNamespace(path="/diagnostics"))), db=db_session)

    row = next(row for row in captured["diagnostic_rows"] if row["item"].plugin == "mqtt-hass")
    assert row["effective_status"] == "warning"
    assert row["message"] == "Plugin was re-enabled; waiting for the next health check."


def test_crowdsec_diagnostics_hide_obsolete_components(monkeypatch, db_session):
    _set(db_session, "plugin.crowdsec.enabled", "true")
    db_session.add(PluginRecord(id="crowdsec", name="CrowdSec", capabilities=["datasource", "action"]))
    db_session.add(Diagnostic(plugin="crowdsec", component="plugin", status="healthy", last_error="ok"))
    db_session.add(Diagnostic(plugin="crowdsec", component="obsolete", status="error", last_error="old error"))
    db_session.add(Diagnostic(plugin="crowdsec", component="lapi", status="healthy", last_error="lapi ok"))
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    monkeypatch.setattr(pages, "render", fake_render)

    pages.diagnostics_page(cast(Any, SimpleNamespace(url=SimpleNamespace(path="/diagnostics"))), db=db_session)

    components = {(row["item"].plugin, row["item"].component) for row in captured["diagnostic_rows"]}
    assert ("crowdsec", "lapi") in components
    assert ("crowdsec", "obsolete") not in components


def test_crowdsec_decision_diagnostic_uses_lapi_component(db_session):
    decisions = import_plugin_module("crowdsec", "services.decisions")

    assert decisions.Diagnostic is Diagnostic

    decisions._update_decision_diagnostic(db_session, "healthy", "lapi ok")
    db_session.flush()

    rows = [(row.plugin, row.component, row.status, row.last_error) for row in db_session.query(Diagnostic).all()]
    assert rows == [("crowdsec", "lapi", "healthy", "lapi ok")]


def test_refresh_health_diagnostics_updates_reenabled_plugin(db_session):
    MqttPlugin = import_plugin_module("mqtt", "plugin").Plugin

    _set(db_session, "plugin.mqtt-hass.enabled", "true")
    db_session.add(Diagnostic(plugin="mqtt-hass", component="plugin", status="disabled", last_error="Plugin is disabled and not running."))
    db_session.commit()
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"mqtt-hass": MqttPlugin()}

    manager.refresh_health_diagnostics(db_session)

    diagnostic = db_session.query(Diagnostic).filter_by(plugin="mqtt-hass", component="plugin").one()
    assert diagnostic.status == "error"
    assert diagnostic.last_error == "MQTT host is not configured"
