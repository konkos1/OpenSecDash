from starlette.requests import Request

from conftest import import_plugin_module


def _request_with_next(next_url: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/crowdsec/decisions/refresh",
            "headers": [],
            "query_string": f"next={next_url}".encode(),
        }
    )


def test_crowdsec_refresh_rejects_protocol_relative_redirect(monkeypatch, db_session):
    routes = import_plugin_module("crowdsec", "routes")
    monkeypatch.setattr(routes, "sync_crowdsec_decisions", lambda db, force=False: (True, "ok"))

    response = routes.crowdsec_decisions_refresh(_request_with_next("//evil.example"), db_session)

    assert response.status_code == 303
    assert response.headers["location"] == "/crowdsec"


def test_crowdsec_refresh_allows_local_redirect(monkeypatch, db_session):
    routes = import_plugin_module("crowdsec", "routes")
    monkeypatch.setattr(routes, "sync_crowdsec_decisions", lambda db, force=False: (True, "ok"))

    response = routes.crowdsec_decisions_refresh(_request_with_next("/crowdsec"), db_session)

    assert response.status_code == 303
    assert response.headers["location"] == "/crowdsec"
