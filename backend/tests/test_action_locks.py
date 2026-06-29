import pytest

from app.services import actions as action_service
from app.services.actions import ActionAlreadyRunning, create_action
from app.services.asset_actions import AssetActionAlreadyRunning, run_asset_action, run_asset_metadata_action


def test_create_action_blocks_duplicate_running_action(monkeypatch, db_session):
    duplicate_was_blocked = False

    def fake_execute_action(db, action):
        nonlocal duplicate_was_blocked
        with pytest.raises(ActionAlreadyRunning):
            create_action(db, "security.ban", "8.8.8.8", "ip", {"duration": "4h"}, confirmed=True)
        duplicate_was_blocked = True
        action.status = "completed"
        action.result = "ok"

    monkeypatch.setattr(action_service, "execute_action", fake_execute_action)

    action = create_action(db_session, "security.ban", "8.8.8.8", "ip", {"duration": "4h"}, confirmed=True)

    assert duplicate_was_blocked is True
    assert action.status == "completed"


def test_asset_global_actions_do_not_overlap():
    def nested_action():
        with pytest.raises(AssetActionAlreadyRunning) as exc_info:
            run_asset_action("mqtt_publish", lambda: None)
        assert exc_info.value.action == "import"

    run_asset_action("import", nested_action)

    # Lock must be released after the previous action finished.
    run_asset_action("mqtt_publish", lambda: "ok")


def test_asset_metadata_lock_is_per_asset():
    def nested_same_asset():
        with pytest.raises(AssetActionAlreadyRunning) as exc_info:
            run_asset_metadata_action(7, lambda: None)
        assert exc_info.value.action == "metadata:7"

    run_asset_metadata_action(7, nested_same_asset)

    # A different asset can still be saved while asset 7 is running.
    def nested_other_asset():
        return run_asset_metadata_action(8, lambda: "ok")

    assert run_asset_metadata_action(7, nested_other_asset) == "ok"
