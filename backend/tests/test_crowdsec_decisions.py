from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.time import utc_now
from app.models.core import CrowdSecDecision
from app.models.settings import Setting
from app.plugins.loader import import_plugin_module
from app.services.actions import create_action

# The CrowdSec decision service now lives in the plugin; load it the same way
# the plugin manager does (see docs/internal/plugin-rework/).
decisions = import_plugin_module(Path(__file__).resolve().parents[2] / "plugins" / "crowdsec", "services.decisions")
_parse_datetime = decisions._parse_datetime
active_decision_for_ip = decisions.active_decision_for_ip


def test_parse_datetime_converts_to_utc_regardless_of_offset():
    # "+02:00" is 2 hours ahead of UTC; the naive result must always be the
    # UTC-equivalent instant, not shifted by the interpreter's local timezone.
    assert _parse_datetime("2026-06-29T14:00:00+02:00") == datetime(2026, 6, 29, 12, 0, 0)
    assert _parse_datetime("2026-06-29T12:00:00Z") == datetime(2026, 6, 29, 12, 0, 0)


def test_unban_requires_active_crowdsec_decision(monkeypatch, db_session):
    monkeypatch.setattr("app.services.actions.execute_action", lambda db, action: None)
    db_session.add(Setting(key="action_dry_run", value="false"))
    db_session.commit()

    with pytest.raises(ValueError, match="No active CrowdSec ban decision"):
        create_action(db_session, "security.unban", "8.8.8.8", "ip", {}, confirmed=True)

    db_session.add(
        CrowdSecDecision(
            decision_id="42",
            ip="8.8.8.8",
            decision_type="ban",
            synced_at=utc_now().replace(tzinfo=None),
            until=(utc_now() + timedelta(hours=1)).replace(tzinfo=None),
        )
    )
    db_session.commit()

    action = create_action(db_session, "security.unban", "8.8.8.8", "ip", {}, confirmed=True)

    assert action.parameters is not None
    assert action.parameters["decision_id"] == "42"


def test_dry_run_unban_does_not_require_active_decision(monkeypatch, db_session):
    monkeypatch.setattr("app.services.actions.execute_action", lambda db, action: None)
    db_session.add(Setting(key="action_dry_run", value="true"))
    db_session.commit()

    action = create_action(db_session, "security.unban", "8.8.8.8", "ip", {}, confirmed=True)

    assert action.action_type == "security.unban"
