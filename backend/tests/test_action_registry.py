from app.api.actions import available_actions
from app.core.template_context import get_setting_value
from app.models.settings import Setting
from app.plugins.base import ActionPlugin
from app.plugins.manager import get_plugin_manager


def test_crowdsec_action_types_are_derived_from_definitions():
    plugin = get_plugin_manager().plugins["crowdsec"]
    assert isinstance(plugin, ActionPlugin)

    assert plugin.action_types == {"security.ban", "crowdsec_ban", "security.unban", "crowdsec_unban"}
    assert plugin.critical_action_types == plugin.action_types


def test_available_api_includes_ban_metadata(db_session):
    actions = available_actions("ip", "1.2.3.4", db_session)

    ban = next(action for action in actions if action["action_type"] == "security.ban")
    parameter = next(parameter for parameter in ban["parameters"] if parameter["name"] == "duration")
    assert ban["critical"] is True
    assert parameter["options"] == ["4h", "24h", "7d"]
    assert parameter["default"] == "4h"


def test_available_actions_respects_unban_availability_hook(db_session):
    db_session.add(Setting(key="action_dry_run", value="false"))
    db_session.commit()

    actions = available_actions("ip", "1.2.3.4", db_session)
    assert "security.unban" not in {action["action_type"] for action in actions}

    db_session.query(Setting).filter_by(key="action_dry_run").update({"value": "true"})
    db_session.commit()
    actions = available_actions("ip", "1.2.3.4", db_session)
    assert "security.unban" in {action["action_type"] for action in actions}
    assert get_setting_value(db_session, "action_dry_run") == "true"


def test_available_actions_hides_crowdsec_when_disabled_outside_dry_run(db_session):
    db_session.add(Setting(key="plugin.crowdsec.enabled", value="false"))
    db_session.add(Setting(key="action_dry_run", value="false"))
    db_session.commit()

    assert available_actions("ip", "1.2.3.4", db_session) == []


def test_available_api_excludes_unsupported_target_type(db_session):
    assert available_actions("asset", "x", db_session) == []
