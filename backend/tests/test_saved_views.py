from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qsl

from app.api import pages
from app.models.events import Event
from app.models.saved_views import SavedView
from app.models.settings import Setting
from app.services.events import apply_event_filters
from app.services.saved_views import view_filters_from_query, view_to_query


def _request(path: str = "/events", query_params: dict[str, str] | None = None):
    return SimpleNamespace(
        query_params=query_params or {},
        headers={},
        url=f"http://testserver{path}",
    )


def test_save_view_stores_only_validated_allowlist_filters_and_overwrites_duplicates(db_session):
    response = pages.save_view(
        cast(Any, _request()),
        scope="events",
        name="HTTP errors",
        filters="status_min=400&country_in=de,RU&range=24h&today=true&not_allowed=value&show_local_ips=true",
        db=db_session,
    )

    view = db_session.query(SavedView).one()
    assert view.filter_json == {"country_in": ["DE", "RU"], "status_code_min": 400, "show_local_ips": True}
    assert response.headers["location"] == "/events?country_in=DE%2CRU&status_min=400&show_local_ips=true"

    pages.save_view(
        cast(Any, _request()),
        scope="events",
        name="HTTP errors",
        filters="status_max=499",
        db=db_session,
    )

    assert db_session.query(SavedView).count() == 1
    assert db_session.query(SavedView).one().filter_json == {"status_code_max": 499}


def test_save_view_without_name_preserves_filters_and_reports_validation_error(db_session):
    response = pages.save_view(
        cast(Any, _request()),
        scope="events",
        name=" ",
        filters="status_min=400&country=DE",
        db=db_session,
    )

    assert response.headers["location"] == "/events?country=DE&status_min=400&view_error=missing_name"
    assert db_session.query(SavedView).count() == 0


def test_save_access_view_without_name_preserves_route_only_filters(db_session):
    response = pages.save_view(
        cast(Any, _request("/access")),
        scope="access",
        name="",
        filters="status_min=404",
        return_query="status_min=404&range=24h&today=true&snapshot_before=2026-07-12T12%3A00%3A00",
        db=db_session,
    )

    assert response.headers["location"] == "/access?status_min=404&range=24h&today=true&snapshot_before=2026-07-12T12%3A00%3A00&view_error=missing_name"


def test_save_view_keeps_validated_route_state_with_engine_filters(db_session):
    response = pages.save_view(
        cast(Any, _request("/access")),
        scope="access",
        name="Recent errors",
        filters="status_min=404",
        return_query="status_min=404&range=24h&today=true&snapshot_before=2026-07-12T12%3A00%3A00",
        db=db_session,
    )

    view = db_session.query(SavedView).one()
    assert view.filter_json == {"status_code_min": 404}
    assert view.query_json == {"range": "24h", "today": "true", "snapshot_before": "2026-07-12T12:00:00"}
    assert response.headers["location"] == "/access?status_min=404&range=24h&today=true&snapshot_before=2026-07-12T12%3A00%3A00"


def test_saved_views_are_listed_per_scope_and_can_be_deleted(db_session):
    events_view = SavedView(name="Events", scope="events", filter_json={"country": "DE"})
    access_view = SavedView(name="Access", scope="access", filter_json={"status_code_min": 400})
    db_session.add_all([events_view, access_view])
    db_session.commit()

    context = pages._saved_view_context(db_session, "events", cast(Any, _request()))
    assert [view.name for view in cast(list[SavedView], context["saved_views"])] == ["Events"]

    response = pages.delete_saved_view(cast(Any, _request()), events_view.id, scope="events", db=db_session)
    assert response.headers["location"] == "/events"
    assert db_session.query(SavedView).filter(SavedView.id == events_view.id).first() is None
    assert db_session.query(SavedView).filter(SavedView.id == access_view.id).first() is not None


def test_view_query_round_trip_remains_compatible_with_event_filters(db_session):
    matching = Event(event_type="access.error", severity="warning", plugin="traefik_log", country="DE", status_code=404)
    other = Event(event_type="access.error", severity="warning", plugin="traefik_log", country="US", status_code=200)
    db_session.add_all([matching, other])
    db_session.commit()
    filters = {"country_in": ["DE"], "status_code_min": 400, "status_code_max": 499}

    restored = view_filters_from_query(parse_qsl(view_to_query(filters)))
    matched_ids = [event.id for event in apply_event_filters(db_session.query(Event), restored).all()]

    assert restored == filters
    assert matched_ids == [matching.id]


def test_plugin_default_views_are_rendered_as_read_only(monkeypatch, db_session):
    db_session.add(Setting(key="plugin.traefik_log.enabled", value="true"))
    db_session.commit()

    class FakeManager:
        def default_views(self):
            return [{"plugin_id": "traefik_log", "scope": "events", "name": "Plugin errors", "filter": {"status_code_min": 400}}]

    captured: dict[str, Any] = {}
    monkeypatch.setattr(pages, "get_plugin_manager", lambda: FakeManager())
    monkeypatch.setattr(pages, "render", lambda request, db, template, **context: captured.update(context) or context)

    pages.events_page(cast(Any, _request()), db=db_session)

    assert captured["plugin_views"] == [
        {
            "name": "Plugin errors",
            "filter_json": {"status_code_min": 400},
            "plugin_id": "traefik_log",
            "href": "/events?status_min=400",
        }
    ]
