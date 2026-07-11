from typing import Any

import pytest

from app.models.events import Event
from app.plugins.base import ActionDefinition, ActionPlugin
from app.services.actions import create_action


class DummyActionPlugin(ActionPlugin):
    action_definitions = (ActionDefinition(action_type="dummy.do", label_key="dummy.do", target_types=frozenset({"ip"}), critical=True),)

    def __init__(self):
        self.after_execute_calls = 0

    def validate_action(self, db, action_type, target, parameters, dry_run):
        if parameters.get("reject"):
            raise ValueError("dummy rejected")
        return {**parameters, "validated": "yes"}

    def prepare_parameters(self, db, action):
        return {**(action.parameters or {}), "prepared_id": action.id}

    def success_event_type(self, action_type):
        return "dummy.completed"

    def action_event_data(self, action):
        return {"dummy_field": "abc"}

    def after_execute(self, db, action):
        self.after_execute_calls += 1


class FakeManager:
    def __init__(self, plugin: ActionPlugin | None, plugin_id: str = "dummy"):
        self._plugin = plugin
        self._plugin_id = plugin_id

    def action_plugin_for(self, action_type: str) -> ActionPlugin | None:
        if self._plugin is not None and action_type in self._plugin.action_types:
            return self._plugin
        return None

    def critical_action_types(self) -> frozenset[str]:
        return self._plugin.critical_action_types if self._plugin is not None else frozenset()

    def plugin_id_for_action(self, action_type: str) -> str:
        return self._plugin_id if self.action_plugin_for(action_type) is not None else "core"

    async def execute_action(self, db, action_type: str, target: str, parameters: dict[str, Any]):
        return None


def _install(monkeypatch, plugin):
    monkeypatch.setattr("app.services.actions.get_plugin_manager", lambda: FakeManager(plugin))


def test_hooks_drive_validation_params_event_and_after_execute(monkeypatch, db_session):
    plugin = DummyActionPlugin()
    _install(monkeypatch, plugin)

    action = create_action(db_session, "dummy.do", "1.2.3.4", "ip", {"note": "x"}, confirmed=True)
    db_session.commit()

    # validate_action normalized the params, prepare_parameters folded in the id
    params = action.parameters or {}
    assert params["validated"] == "yes"
    assert params["prepared_id"] == action.id
    assert action.plugin_id == "dummy"

    event = db_session.query(Event).filter_by(event_type="dummy.completed").one()
    assert event.data_json["dummy_field"] == "abc"
    assert event.data_json["action_id"] == action.id
    assert event.plugin == "dummy"

    # dry-run (the default) must not call the real-execution hook
    assert plugin.after_execute_calls == 0


def test_validate_action_rejection_propagates(monkeypatch, db_session):
    _install(monkeypatch, DummyActionPlugin())

    with pytest.raises(ValueError, match="dummy rejected"):
        create_action(db_session, "dummy.do", "1.2.3.4", "ip", {"reject": True}, confirmed=True)


def test_unknown_action_type_is_rejected(monkeypatch, db_session):
    # A plugin is present but does not handle this action type. Accepting it
    # anyway would record a bogus "completed" no-op and, worse, skip the
    # plugin-declared safety gates (confirmation, IP validation) - exactly
    # what happens when the owning plugin is disabled via
    # OSD_PLUGIN_<NAME>_DISABLED. So create_action must reject it.
    _install(monkeypatch, DummyActionPlugin())

    with pytest.raises(ValueError, match="Unknown action type: misc.thing"):
        create_action(db_session, "misc.thing", "host-1", "host", {}, confirmed=False)

    assert db_session.query(Event).count() == 0


def test_ban_without_owning_plugin_is_rejected(monkeypatch, db_session):
    # Regression for the phase-1-6 review finding: with no plugin loaded at
    # all (e.g. crowdsec disabled via OSD_PLUGIN_CROWDSEC_DISABLED),
    # "security.ban" used to sail through without confirmation and without
    # global-IP validation, ending up as a fake-"completed" core event.
    _install(monkeypatch, None)

    with pytest.raises(ValueError, match="Unknown action type: security.ban"):
        create_action(db_session, "security.ban", "192.168.1.50", "ip", {}, confirmed=False)

    assert db_session.query(Event).count() == 0
