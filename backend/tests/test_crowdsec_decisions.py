import json
from datetime import timedelta

import pytest

from app.core.time import utc_now
from app.models.core import CrowdSecDecision
from app.models.settings import Setting
from app.services.actions import create_action
from app.services.crowdsec_decisions import active_decision_for_ip, crowdsec_cscli_status, sync_crowdsec_decisions


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_sync_crowdsec_decisions_stores_active_bans(monkeypatch, db_session):
    payload = [
        {
            "id": 42,
            "scope": "Ip",
            "value": "8.8.8.8",
            "type": "ban",
            "origin": "cscli",
            "scenario": "manual",
            "duration": "4h",
            "until": "2026-06-29T12:00:00Z",
        },
        {"id": 43, "scope": "Ip", "value": "1.1.1.1", "type": "captcha"},
    ]

    def fake_run(cmd, capture_output, text, timeout):
        assert cmd == ["/usr/local/bin/cscli", "decisions", "list", "-o", "json"]
        return Completed(stdout=json.dumps(payload))

    monkeypatch.setattr("app.services.crowdsec_decisions.subprocess.run", fake_run)

    ok, message = sync_crowdsec_decisions(db_session, force=True)
    db_session.commit()

    assert ok is True
    assert "1 active" in message
    decision = active_decision_for_ip(db_session, "8.8.8.8")
    assert decision is not None
    assert decision.decision_id == "42"
    assert decision.scenario == "manual"
    assert active_decision_for_ip(db_session, "1.1.1.1") is None
    status = crowdsec_cscli_status(db_session)
    assert status is not None
    assert status.status == "healthy"


def test_sync_crowdsec_decisions_records_cscli_error(monkeypatch, db_session):
    def fake_run(cmd, capture_output, text, timeout):
        return Completed(returncode=1, stderr="cscli unavailable")

    monkeypatch.setattr("app.services.crowdsec_decisions.subprocess.run", fake_run)

    ok, message = sync_crowdsec_decisions(db_session, force=True)
    db_session.commit()

    assert ok is False
    assert message == "cscli unavailable"
    status = crowdsec_cscli_status(db_session)
    assert status is not None
    assert status.status == "error"
    assert status.last_error == "cscli unavailable"


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
