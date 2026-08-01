import json
from datetime import timedelta

from app.core.time import utc_now
from app.models.core import AggregationDaily, GeoIPCache, Insight, Notification, NotificationRule
from app.models.events import Event
from app.models.settings import Setting
from app.services.events import store_event
from app.services.notifications import invalidate_rules_cache
from conftest import import_plugin_module

geoip_service = import_plugin_module("geoip", "services.geoip")
iplocate = import_plugin_module("geoip", "services.providers.iplocate")

enrich_event_values = geoip_service.enrich_event_values
enrich_pending_events = geoip_service.enrich_pending_events
lookup_geoip = geoip_service.lookup_geoip
normalize_asn = geoip_service.normalize_asn
normalize_city = geoip_service.normalize_city
normalize_isp = geoip_service.normalize_isp
normalize_lookup_target = geoip_service.normalize_lookup_target


def test_geoip_normalizes_asn_city_and_truncates_isp():
    assert normalize_asn("15169 Google LLC") == "AS15169"
    assert normalize_asn("AS8075 Microsoft") == "AS8075"
    assert normalize_asn("not-an-asn") is None
    assert normalize_city("  Berlin  ") == "Berlin"
    assert len(normalize_city("x" * 300) or "") == 255
    assert normalize_isp("  Example ISP  ") == "Example ISP"
    assert len(normalize_isp("x" * 300) or "") == 255


def test_geoip_only_accepts_globally_routable_lookup_targets():
    assert normalize_lookup_target("100.64.0.1") is None
    assert normalize_lookup_target("100.64.0.0/10") is None
    assert normalize_lookup_target("224.0.0.1") is None
    assert normalize_lookup_target("8.8.8.8") == ("8.8.8.8", "8.8.8.8")
    assert normalize_lookup_target("8.8.8.0/24") == ("8.8.8.0/24", "8.8.8.1")


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


def _fake_iplocate_response(monkeypatch, payload: dict, status_code: int = 200) -> list[dict]:
    calls: list[dict] = []

    class _Response:
        headers: dict[str, str] = {}

        def __init__(self):
            self.status_code = status_code

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


def test_enrich_pending_events_reconciles_country_rollup_insight_and_notifications(db_session):
    _cached_geoip_setup(db_session)
    db_session.add_all(
        [
            Setting(key="notifications.enabled", value="true"),
            Setting(key="notifications.smtp_host", value="smtp.example"),
            Setting(key="notifications.smtp_sender", value="sender@example"),
            Setting(key="notifications.smtp_recipient", value="admin@example"),
            NotificationRule(
                rule_id="test.all_geoblocks",
                name="All geoblocks",
                source="event",
                match_types=["security.geoblock"],
                min_severity="warning",
            ),
            NotificationRule(
                rule_id="test.us_geoblocks",
                name="US geoblocks",
                source="event",
                match_types=["security.geoblock"],
                min_severity="warning",
                countries=["US"],
            ),
        ]
    )
    db_session.commit()
    invalidate_rules_cache()

    event = store_event(
        db_session,
        source="test",
        plugin="geoblock",
        event_type="security.geoblock",
        severity="warning",
        event_time=utc_now().replace(tzinfo=None),
        ip="8.8.8.8",
    )
    db_session.commit()

    assert db_session.query(AggregationDaily).filter_by(metric="country", key="US").count() == 0
    assert " from US" not in db_session.query(Insight).filter_by(type="geoblock_denied_request").one().description
    assert db_session.query(Notification).filter_by(rule_id="test.all_geoblocks").count() == 1
    assert db_session.query(Notification).filter_by(rule_id="test.us_geoblocks").count() == 0

    assert enrich_pending_events(db_session, limit=10) == 1

    assert db_session.query(AggregationDaily).filter_by(metric="summary", key="total_events").one().value == 1
    assert db_session.query(AggregationDaily).filter_by(metric="country", key="US").one().value == 1
    assert " from US" in db_session.query(Insight).filter_by(type="geoblock_denied_request").one().description
    all_geoblocks = db_session.query(Notification).filter_by(rule_id="test.all_geoblocks").one()
    assert all_geoblocks.payload["country"] == "US"
    us_geoblocks = db_session.query(Notification).filter_by(rule_id="test.us_geoblocks").one()
    assert us_geoblocks.payload["event_id"] == event.id
    invalidate_rules_cache()


def test_enrich_pending_events_noop_when_geoip_disabled(db_session):
    db_session.add(Setting(key="plugin.geoip.enabled", value="false"))
    event = Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False)
    db_session.add(event)
    db_session.commit()

    assert enrich_pending_events(db_session, limit=10) == 0
    db_session.refresh(event)
    assert event.geoip_checked is False
    assert event.country is None


def test_enrich_pending_events_retries_after_missing_iplocate_key_is_configured(db_session, monkeypatch):
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.geoip.provider", value="iplocate"),
            Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False),
        ]
    )
    db_session.commit()

    assert enrich_pending_events(db_session, limit=10) == 0
    event = db_session.query(Event).one()
    assert event.geoip_checked is False
    assert db_session.query(GeoIPCache).count() == 0

    db_session.add(Setting(key="plugin.geoip.iplocate_api_key", value="configured-later"))
    db_session.commit()
    _fake_iplocate_response(monkeypatch, {"country_code": "US"})

    assert enrich_pending_events(db_session, limit=10) == 1
    db_session.refresh(event)
    assert event.geoip_checked is True
    assert event.country == "US"


def test_enrich_pending_events_retries_immediately_after_rejected_iplocate_key_is_replaced(db_session, monkeypatch):
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.geoip.provider", value="iplocate"),
            Setting(key="plugin.geoip.iplocate_api_key", value="rejected-key"),
            Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False),
            Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="1.1.1.1", geoip_checked=False),
        ]
    )
    db_session.commit()
    rejected_calls = _fake_iplocate_response(monkeypatch, {}, status_code=401)

    assert enrich_pending_events(db_session, limit=10) == 0
    assert len(rejected_calls) == 1
    assert db_session.query(Event).filter_by(geoip_checked=False).count() == 2
    assert db_session.query(GeoIPCache).count() == 0

    db_session.query(Setting).filter_by(key="plugin.geoip.iplocate_api_key").one().value = "replacement-key"
    db_session.commit()
    successful_calls = _fake_iplocate_response(monkeypatch, {"country_code": "US"})

    assert enrich_pending_events(db_session, limit=10) == 2
    assert len(successful_calls) == 2
    assert db_session.query(Event).filter_by(geoip_checked=True).count() == 2


def test_enrich_pending_events_retries_after_cached_provider_failure_expires(db_session, monkeypatch):
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.geoip.provider", value="iplocate"),
            Setting(key="plugin.geoip.iplocate_api_key", value="test-key"),
            Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False),
        ]
    )
    db_session.commit()

    def fail_request(*args, **kwargs):
        raise iplocate.requests.ConnectionError("provider unavailable")

    monkeypatch.setattr(iplocate.requests, "get", fail_request)
    assert enrich_pending_events(db_session, limit=10) == 0
    event = db_session.query(Event).one()
    assert event.geoip_checked is False
    cached = db_session.query(GeoIPCache).one()
    assert cached.error == "IPLocate request could not be sent"

    # An unexpired error cache avoids hammering the provider but must not make
    # the event final.
    assert enrich_pending_events(db_session, limit=10) == 0
    assert event.geoip_checked is False

    cached.expires_at = (utc_now() - timedelta(seconds=1)).replace(tzinfo=None)
    db_session.commit()
    _fake_iplocate_response(monkeypatch, {"country_code": "US"})

    assert enrich_pending_events(db_session, limit=10) == 1
    db_session.refresh(event)
    assert event.geoip_checked is True
    assert event.country == "US"


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
