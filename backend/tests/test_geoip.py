import threading
from datetime import timedelta

from app.core.time import utc_now
from app.models.core import GeoIPCache
from app.models.events import Event
from app.models.settings import Setting
from app.services.geoip import enrich_event_values, enrich_pending_events, lookup_geoip, normalize_asn, normalize_city, normalize_isp


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


class _CountingLock:
    """Wraps a real threading.Lock so the test can count with-block entries."""

    def __init__(self):
        self._lock = threading.Lock()
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self._lock.__enter__()

    def __exit__(self, *args):
        return self._lock.__exit__(*args)

    def locked(self):
        return self._lock.locked()


def test_enrich_pending_events_commits_per_event_under_write_lock(db_session):
    # A write_lock is only ever held around each individual commit, never
    # around the (potentially slow, network-bound) lookup itself - otherwise
    # a slow/unreachable GeoIP provider would make other threads wait on the
    # SQLite write lock for that long too.
    _cached_geoip_setup(db_session)
    for _ in range(3):
        db_session.add(Event(source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8", geoip_checked=False))
    db_session.commit()

    lock = _CountingLock()
    processed = enrich_pending_events(db_session, limit=10, write_lock=lock)

    assert processed == 3
    assert lock.enter_count == 3
    assert lock.locked() is False  # released again after the last commit
