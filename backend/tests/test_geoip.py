import json
from datetime import timedelta

from app.core.time import utc_now
from app.models.core import GeoIPCache
from app.models.events import Event
from app.models.settings import Setting
from conftest import import_plugin_module

geoip_service = import_plugin_module("geoip", "services.geoip")
iplocate = import_plugin_module("geoip", "services.providers.iplocate")

enrich_event_values = geoip_service.enrich_event_values
enrich_pending_events = geoip_service.enrich_pending_events
lookup_geoip = geoip_service.lookup_geoip
normalize_asn = geoip_service.normalize_asn
normalize_city = geoip_service.normalize_city
normalize_isp = geoip_service.normalize_isp


def test_geoip_normalizes_asn_city_and_truncates_isp():
    assert normalize_asn("15169 Google LLC") == "AS15169"
    assert normalize_asn("AS8075 Microsoft") == "AS8075"
    assert normalize_asn("not-an-asn") is None
    assert normalize_city("  Berlin  ") == "Berlin"
    assert len(normalize_city("x" * 300) or "") == 255
    assert normalize_isp("  Example ISP  ") == "Example ISP"
    assert len(normalize_isp("x" * 300) or "") == 255


def test_geoip_cache_is_used_and_plugin_values_win(db_session):
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.geoip.provider", value="ip-api"),
            GeoIPCache(
                lookup_key="8.8.8.8",
                provider="ip-api",
                country="US",
                city="Mountain View",
                asn="AS15169",
                isp="Google LLC",
                looked_up_at=utc_now().replace(tzinfo=None),
                expires_at=(utc_now() + timedelta(days=1)).replace(tzinfo=None),
            ),
        ]
    )
    db_session.commit()

    assert lookup_geoip(db_session, "8.8.8.8", require_city=True, require_asn=True, require_isp=True) == ("US", "Mountain View", "AS15169", "Google LLC")

    values = {"ip": "8.8.8.8", "country": "DE"}
    enrich_event_values(db_session, values)
    assert values == {"ip": "8.8.8.8", "country": "DE", "city": "Mountain View", "asn": "AS15169", "isp": "Google LLC"}

    producer_values = {"ip": "8.8.8.8", "country": "DE", "city": "Berlin", "asn": "AS64500", "isp": "Producer ISP"}
    enrich_event_values(db_session, producer_values)
    assert producer_values == {"ip": "8.8.8.8", "country": "DE", "city": "Berlin", "asn": "AS64500", "isp": "Producer ISP"}


def test_geoip_cache_of_another_provider_is_refreshed_instead_of_reused(db_session, monkeypatch):
    _cached_geoip_setup(db_session)
    db_session.commit()
    db_session.query(Setting).filter_by(key="plugin.geoip.provider").one().value = "iplocate"
    db_session.add(Setting(key="plugin.geoip.iplocate_api_key", value="dummy-iplocate-key"))
    db_session.commit()
    calls = _fake_iplocate_response(
        monkeypatch,
        {"country_code": "FR", "city": "Paris", "asn": {"asn": "AS64500"}, "company": {"name": "Example EU"}},
    )

    switched = lookup_geoip(db_session, "8.8.8.8", require_city=True, require_asn=True, require_isp=True)
    db_session.commit()

    # The ip-api row is not a hit for IPLocate: the same row is refreshed.
    assert switched == ("FR", "Paris", "AS64500", "Example EU")
    assert len(calls) == 1
    row = db_session.query(GeoIPCache).filter_by(lookup_key="8.8.8.8").one()
    assert (row.provider, row.country) == ("iplocate", "FR")

    # A row of the selected provider stays a hit - no second request.
    assert lookup_geoip(db_session, "8.8.8.8", require_city=True) == ("FR", "Paris", "AS64500", "Example EU")
    assert len(calls) == 1


def _fake_iplocate_response(monkeypatch, payload: dict) -> list[dict]:
    calls: list[dict] = []

    class _Response:
        headers: dict[str, str] = {}
        status_code = 200

        def iter_content(self, chunk_size):
            yield json.dumps(payload).encode()

        def close(self):
            return None

    def fake_get(*args, **kwargs):
        calls.append(kwargs)
        return _Response()

    monkeypatch.setattr(iplocate.requests, "get", fake_get)
    return calls


def _cached_geoip_setup(db_session):
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.geoip.provider", value="ip-api"),
            GeoIPCache(
                lookup_key="8.8.8.8",
                provider="ip-api",
                country="US",
                city="Mountain View",
                asn="AS15169",
                isp="Google LLC",
                looked_up_at=utc_now().replace(tzinfo=None),
                expires_at=(utc_now() + timedelta(days=1)).replace(tzinfo=None),
            ),
        ]
    )


def test_enrich_pending_events_backfills_from_cache_and_marks_checked(db_session):
    _cached_geoip_setup(db_session)
    event = Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False)
    db_session.add(event)
    db_session.commit()

    processed = enrich_pending_events(db_session, limit=10)
    db_session.commit()

    assert processed == 1
    db_session.refresh(event)
    assert event.geoip_checked is True
    assert (event.country, event.city, event.asn, event.isp) == ("US", "Mountain View", "AS15169", "Google LLC")


def test_enrich_pending_events_noop_when_geoip_disabled(db_session):
    db_session.add(Setting(key="plugin.geoip.enabled", value="false"))
    event = Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False)
    db_session.add(event)
    db_session.commit()

    assert enrich_pending_events(db_session, limit=10) == 0
    db_session.refresh(event)
    assert event.geoip_checked is False
    assert event.country is None


def test_enrich_pending_events_ignores_already_checked_events(db_session):
    _cached_geoip_setup(db_session)
    event = Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=True)
    db_session.add(event)
    db_session.commit()

    assert enrich_pending_events(db_session, limit=10) == 0
    db_session.refresh(event)
    assert event.country is None


def test_enrich_pending_events_respects_limit(db_session):
    _cached_geoip_setup(db_session)
    for _ in range(3):
        db_session.add(Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False))
    db_session.commit()

    assert enrich_pending_events(db_session, limit=2) == 2


def test_enrich_pending_events_commits_after_each_event(db_session):
    # Committing per-event (rather than once for the whole batch) means a
    # slow/unreachable GeoIP provider only ever risks losing one event's
    # progress, not the whole batch - and only ever holds the app-wide SQLite
    # write lock (see app.database.session) for one event's commit at a time.
    _cached_geoip_setup(db_session)
    for _ in range(3):
        db_session.add(Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False))
    db_session.commit()

    processed = enrich_pending_events(db_session, limit=10)

    assert processed == 3
    db_session.rollback()  # would discard the events if enrich_pending_events hadn't already committed them
    assert db_session.query(Event).filter_by(geoip_checked=True).count() == 3
