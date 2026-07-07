from types import SimpleNamespace
from typing import Any, cast

from app.models.events import Event
from conftest import import_plugin_module
from app.models.settings import Setting


def _set(db_session, key: str, value: str) -> None:
    db_session.add(Setting(key=key, value=value))
    db_session.commit()


def _request(query_params: dict[str, str] | None = None):
    return SimpleNamespace(query_params=query_params or {}, url=SimpleNamespace(path="/access"))


def test_access_page_hides_local_ips_by_default_when_traefik_setting_enabled(monkeypatch, db_session):
    _set(db_session, "plugin.traefik_log.enabled", "true")
    _set(db_session, "plugin.traefik_log.hide_local_ips_default", "true")
    db_session.add_all(
        [
            Event(source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="10.0.0.5"),
            Event(source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="8.8.8.8"),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    routes = import_plugin_module("traefik_log", "routes")
    monkeypatch.setattr(routes, "render", fake_render)

    routes.access_page(cast(Any, _request()), db=db_session)

    assert captured["hide_local_ips"] is True
    assert [event.ip for event in captured["events"]] == ["8.8.8.8"]


def test_access_page_can_disable_default_local_ip_filter(monkeypatch, db_session):
    _set(db_session, "plugin.traefik_log.enabled", "true")
    _set(db_session, "plugin.traefik_log.hide_local_ips_default", "true")
    db_session.add_all(
        [
            Event(source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="10.0.0.5"),
            Event(source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="8.8.8.8"),
        ]
    )
    db_session.commit()
    captured = {}

    def fake_render(request, db, template, **context):
        captured.update(context)
        return context

    routes = import_plugin_module("traefik_log", "routes")
    monkeypatch.setattr(routes, "render", fake_render)

    routes.access_page(cast(Any, _request({"local_ip_filter": "1"})), db=db_session)

    assert captured["hide_local_ips"] is False
    assert sorted(event.ip for event in captured["events"]) == ["10.0.0.5", "8.8.8.8"]
