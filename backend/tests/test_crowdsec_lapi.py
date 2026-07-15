import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.models.core import Diagnostic
from app.models.settings import Setting
from app.plugins.loader import import_plugin_module
from app.plugins.manager import PluginManager

# CrowdSec's LAPI client and decision service now live in the plugin; load them
# the same way the plugin manager does (see docs/internal/plugin-rework/).
_CROWDSEC = Path(__file__).resolve().parents[2] / "plugins" / "crowdsec"
lapi_module = import_plugin_module(_CROWDSEC, "services.lapi")
decisions = import_plugin_module(_CROWDSEC, "services.decisions")
LapiError = lapi_module.LapiError
lapi_active_ban_decisions = lapi_module.lapi_active_ban_decisions
lapi_login = lapi_module.lapi_login
validate_lapi_url = lapi_module.validate_lapi_url
active_decision_for_ip = decisions.active_decision_for_ip
crowdsec_lapi_status = decisions.crowdsec_lapi_status
sync_crowdsec_decisions = decisions.sync_crowdsec_decisions


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
    def fake_post(url, json, timeout, allow_redirects):
        assert url == "http://lapi:8080/v1/watchers/login"
        assert json == {"machine_id": "opensecdash", "password": "pw"}
        assert allow_redirects is False
        return FakeResponse(payload={"token": "jwt-token"})

    monkeypatch.setattr(lapi_module.requests, "post", fake_post)
    assert lapi_login("http://lapi:8080/", "opensecdash", "pw") == "jwt-token"

    monkeypatch.setattr(lapi_module.requests, "post", lambda *a, **k: FakeResponse(status_code=403))
    with pytest.raises(LapiError, match="rejected the credentials"):
        lapi_login("http://lapi:8080", "opensecdash", "wrong")

    with pytest.raises(LapiError, match="must be configured"):
        lapi_login("http://lapi:8080", "", "")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("file:///etc/passwd", "must use http or https"),
        ("http:///missing-host", "must include a host"),
        ("http://user:secret@lapi:8080", "must not contain credentials"),
        ("http://lapi:8080?target=other", "must not contain a query or fragment"),
    ],
)
def test_lapi_url_rejects_unsafe_base_urls(url, message):
    with pytest.raises(LapiError, match=message):
        validate_lapi_url(url)


def test_lapi_url_accepts_same_host_http_and_https():
    assert validate_lapi_url("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"
    assert validate_lapi_url("https://crowdsec.internal:8443") == "https://crowdsec.internal:8443"


def test_lapi_login_rejects_redirect(monkeypatch):
    monkeypatch.setattr(lapi_module.requests, "post", lambda *args, **kwargs: FakeResponse(status_code=302))

    with pytest.raises(LapiError, match="refused an HTTP redirect"):
        lapi_login("http://lapi:8080", "opensecdash", "pw")


def test_lapi_mutations_reject_redirects(monkeypatch):
    monkeypatch.setattr(lapi_module.requests, "post", lambda *args, **kwargs: FakeResponse(status_code=307))
    with pytest.raises(LapiError, match="refused an HTTP redirect"):
        lapi_module.lapi_add_ban("http://lapi:8080", "token", "8.8.8.8", "4h", "test")

    monkeypatch.setattr(lapi_module.requests, "delete", lambda *args, **kwargs: FakeResponse(status_code=302))
    with pytest.raises(LapiError, match="refused an HTTP redirect"):
        lapi_module.lapi_delete_decision("http://lapi:8080", "token", "42")


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

    def fake_get(url, params, headers, timeout, allow_redirects):
        assert url == "http://lapi:8080/v1/alerts"
        assert params["has_active_decision"] == "true"
        assert headers == {"Authorization": "Bearer jwt-token"}
        assert allow_redirects is False
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
    status = crowdsec_lapi_status(db_session)
    assert status is not None
    assert status.status == "healthy"


def test_sync_crowdsec_decisions_records_lapi_error(monkeypatch, db_session):
    _configure_lapi(db_session)

    def failing_login(url, login, pw):
        raise LapiError("CrowdSec LAPI not reachable at http://lapi:8080: connection refused")

    monkeypatch.setattr(lapi_module, "lapi_login", failing_login)

    ok, message = sync_crowdsec_decisions(db_session, force=True)
    db_session.commit()

    assert ok is False
    assert "not reachable" in message


def test_periodic_lapi_error_does_not_replace_log_parser_diagnostic(monkeypatch, db_session):
    _configure_lapi(db_session)
    db_session.add(Setting(key="plugin.crowdsec.enabled", value="true"))
    db_session.add(Diagnostic(plugin="crowdsec", component="plugin", status="healthy", last_error="CrowdSec log readable"))
    db_session.commit()

    def failing_login(url, login, pw):
        raise LapiError("CrowdSec LAPI not reachable at http://lapi:8080: connection refused")

    monkeypatch.setattr(lapi_module, "lapi_login", failing_login)
    plugin_module = import_plugin_module(_CROWDSEC, "plugin")
    plugin = plugin_module.Plugin()
    manager = PluginManager(Path("/not-used"))
    manager.plugins = {"crowdsec": plugin}

    manager._run_periodic_tick(db_session, plugin)
    manager._run_periodic_tick(db_session, plugin)

    plugin_diagnostic = db_session.query(Diagnostic).filter_by(plugin="crowdsec", component="plugin").one()
    lapi_diagnostic = db_session.query(Diagnostic).filter_by(plugin="crowdsec", component="lapi").one()
    assert plugin_diagnostic.status == "healthy"
    assert plugin_diagnostic.last_error == "CrowdSec log readable"
    assert lapi_diagnostic.status == "error"
    assert "not reachable" in (lapi_diagnostic.last_error or "")


def test_plugin_execute_ban_and_unban_via_lapi(monkeypatch):
    # Load through the plugin loader so the plugin's relative imports resolve.
    module = import_plugin_module(_CROWDSEC, "plugin")
    plugin = module.Plugin()

    calls: list[tuple] = []
    monkeypatch.setattr(lapi_module, "lapi_login", lambda url, login, pw: "jwt-token")
    monkeypatch.setattr(lapi_module, "lapi_add_ban", lambda url, token, ip, duration, reason: calls.append(("ban", ip, duration)))
    monkeypatch.setattr(lapi_module, "lapi_delete_decision", lambda url, token, decision_id: calls.append(("unban", decision_id)))

    class Ctx:
        def __init__(self):
            self.settings = {"lapi_url": "http://lapi:8080", "lapi_login": "opensecdash", "lapi_password": "pw"}

        def get(self, key, default=""):
            return self.settings.get(key, default)

    result = asyncio.run(plugin.execute(Ctx(), "security.ban", "8.8.8.8", {"duration": "4h"}))
    assert result == {"status": "completed", "result": "LAPI ban created for 8.8.8.8"}

    result = asyncio.run(plugin.execute(Ctx(), "security.unban", "8.8.8.8", {"decision_id": "42"}))
    assert result == {"status": "completed", "result": "LAPI decision 42 deleted"}

    assert calls == [("ban", "8.8.8.8", "4h"), ("unban", "42")]
