import asyncio
from typing import Any

import pytest

import app.services.crowdsec_lapi as lapi_module
from app.models.settings import Setting
from app.services.crowdsec_decisions import active_decision_for_ip, sync_crowdsec_decisions
from app.services.crowdsec_lapi import LapiError, lapi_active_ban_decisions, lapi_login


class FakeResponse:
    def __init__(self, status_code=200, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_lapi_login_returns_token_and_maps_errors(monkeypatch):
    def fake_post(url, json, timeout):
        assert url == "http://lapi:8080/v1/watchers/login"
        assert json == {"machine_id": "opensecdash", "password": "pw"}
        return FakeResponse(payload={"token": "jwt-token"})

    monkeypatch.setattr(lapi_module.requests, "post", fake_post)
    assert lapi_login("http://lapi:8080/", "opensecdash", "pw") == "jwt-token"

    monkeypatch.setattr(lapi_module.requests, "post", lambda *a, **k: FakeResponse(status_code=403))
    with pytest.raises(LapiError, match="rejected the credentials"):
        lapi_login("http://lapi:8080", "opensecdash", "wrong")

    with pytest.raises(LapiError, match="must be configured"):
        lapi_login("http://lapi:8080", "", "")


def test_lapi_active_ban_decisions_flattens_alerts(monkeypatch):
    alerts = [
        {
            "scenario": "crowdsecurity/ssh-bf",
            "decisions": [
                {"id": 7, "type": "ban", "scope": "Ip", "value": "8.8.8.8", "origin": "crowdsec", "duration": "3h58m", "until": "2026-07-05T20:00:00Z"},
                {"id": 8, "type": "captcha", "scope": "Ip", "value": "9.9.9.9"},
            ],
        },
    ]

    def fake_get(url, params, headers, timeout):
        assert url == "http://lapi:8080/v1/alerts"
        assert params["has_active_decision"] == "true"
        assert headers == {"Authorization": "Bearer jwt-token"}
        return FakeResponse(payload=alerts)

    monkeypatch.setattr(lapi_module.requests, "get", fake_get)
    decisions = lapi_active_ban_decisions("http://lapi:8080", "jwt-token")

    assert len(decisions) == 1  # captcha filtered out
    assert decisions[0]["id"] == 7
    assert decisions[0]["value"] == "8.8.8.8"
    assert decisions[0]["scenario"] == "crowdsecurity/ssh-bf"


def _configure_lapi(db_session):
    db_session.add_all(
        [
            Setting(key="plugin.crowdsec.lapi_url", value="http://lapi:8080"),
            Setting(key="plugin.crowdsec.lapi_login", value="opensecdash"),
            Setting(key="plugin.crowdsec.lapi_password", value="pw"),
        ]
    )
    db_session.commit()


def test_sync_crowdsec_decisions_uses_lapi_by_default(monkeypatch, db_session):
    _configure_lapi(db_session)
    monkeypatch.setattr(lapi_module, "lapi_login", lambda url, login, pw: "jwt-token")
    monkeypatch.setattr(
        lapi_module,
        "lapi_active_ban_decisions",
        lambda url, token: [
            {"id": 42, "value": "8.8.8.8", "scope": "Ip", "type": "ban", "origin": "crowdsec", "scenario": "crowdsecurity/ssh-bf", "reason": "crowdsecurity/ssh-bf", "duration": "4h", "until": "2026-07-05T20:00:00Z", "raw": {"id": 42}},
        ],
    )

    ok, message = sync_crowdsec_decisions(db_session, force=True)
    db_session.commit()

    assert ok is True
    assert "1 active" in message
    decision = active_decision_for_ip(db_session, "8.8.8.8")
    assert decision is not None
    assert decision.decision_id == "42"
    assert decision.scenario == "crowdsecurity/ssh-bf"


def test_sync_crowdsec_decisions_records_lapi_error(monkeypatch, db_session):
    _configure_lapi(db_session)

    def failing_login(url, login, pw):
        raise LapiError("CrowdSec LAPI not reachable at http://lapi:8080: connection refused")

    monkeypatch.setattr(lapi_module, "lapi_login", failing_login)

    ok, message = sync_crowdsec_decisions(db_session, force=True)
    db_session.commit()

    assert ok is False
    assert "not reachable" in message


def test_plugin_execute_ban_and_unban_via_lapi(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("crowdsec_plugin_test", "../plugins/crowdsec/plugin.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = module.Plugin()

    calls: list[tuple] = []
    monkeypatch.setattr(lapi_module, "lapi_login", lambda url, login, pw: "jwt-token")
    monkeypatch.setattr(lapi_module, "lapi_add_ban", lambda url, token, ip, duration, reason: calls.append(("ban", ip, duration)))
    monkeypatch.setattr(lapi_module, "lapi_delete_decision", lambda url, token, decision_id: calls.append(("unban", decision_id)))

    class Ctx:
        def __init__(self):
            self.settings = {"connection_mode": "lapi", "lapi_url": "http://lapi:8080", "lapi_login": "opensecdash", "lapi_password": "pw"}

        def get(self, key, default=""):
            return self.settings.get(key, default)

    result = asyncio.run(plugin.execute(Ctx(), "security.ban", "8.8.8.8", {"duration": "4h"}))
    assert result == {"status": "completed", "result": "LAPI ban created for 8.8.8.8"}

    result = asyncio.run(plugin.execute(Ctx(), "security.unban", "8.8.8.8", {"decision_id": "42"}))
    assert result == {"status": "completed", "result": "LAPI decision 42 deleted"}

    assert calls == [("ban", "8.8.8.8", "4h"), ("unban", "42")]
