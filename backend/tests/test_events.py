from datetime import datetime, timedelta

from app.core.time import utc_now
from app.models.core import AggregationDaily, GeoIPCache, Insight
from app.models.events import Event
from app.models.settings import Setting
from app.services.events import (
    apply_event_filters,
    classify_access_status,
    normalize_event_time,
    store_event,
    tokenize_search_expression,
    update_rollups,
)


def disable_geoip(db_session):
    db_session.add(Setting(key="plugin.geoip.enabled", value="false"))
    db_session.commit()


def test_normalize_event_time_applies_assumed_timezone_only_when_naive():
    # A naive timestamp (no offset in the source, e.g. GeoBlock's own log format)
    # is interpreted in the configured assumed timezone before converting to UTC.
    assert normalize_event_time("2026-06-20 04:00:54", assume_tz="Europe/Berlin") == datetime(2026, 6, 20, 2, 0, 54)
    assert normalize_event_time("2026-06-20 04:00:54", assume_tz="UTC") == datetime(2026, 6, 20, 4, 0, 54)
    assert normalize_event_time("2026-06-20 04:00:54") == datetime(2026, 6, 20, 4, 0, 54)

    # A timestamp that already carries an explicit offset (Traefik/CrowdSec style)
    # is unaffected by `assume_tz` - it's unambiguous already.
    assert normalize_event_time("2026-06-20T04:00:54+02:00", assume_tz="America/New_York") == datetime(2026, 6, 20, 2, 0, 54)
    assert normalize_event_time("2026-06-20T04:00:54Z", assume_tz="America/New_York") == datetime(2026, 6, 20, 4, 0, 54)


def test_store_event_never_enriches_geoip_inline(db_session):
    # Ingestion no longer does GeoIP enrichment inline (see geoip.enrich_pending_events) -
    # a first-time import of a large log could otherwise mean thousands of
    # synchronous lookup HTTP calls in a row. Even with GeoIP enabled and a
    # cache hit available, a freshly stored event must come back unenriched
    # and flagged for the background backfill pass to pick up.
    db_session.add_all(
        [
            Setting(key="plugin.geoip.enabled", value="true"),
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

    event = store_event(db_session, source="test", plugin="traefik_log", event_type="access.allowed", ip="8.8.8.8")

    assert event.country is None
    assert event.city is None
    assert event.geoip_checked is False


def test_store_event_deduplicates_and_updates_rollups_and_insights(db_session):
    disable_geoip(db_session)
    event_time = datetime(2026, 1, 2, 3, 4, 5)

    first = store_event(
        db_session,
        source="test",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        ip="8.8.8.8",
        country="US",
        event_time=event_time,
        data_json={"scenario": "ssh-bf", "duration": "4h"},
        raw_data="same-line",
    )
    duplicate = store_event(
        db_session,
        source="test",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        ip="8.8.8.8",
        country="US",
        event_time=event_time,
        data_json={"scenario": "ssh-bf", "duration": "4h"},
        raw_data="same-line",
    )
    db_session.commit()

    assert first.id == duplicate.id
    assert getattr(duplicate, "_opensecdash_created") is False
    assert db_session.query(Event).count() == 1
    assert db_session.query(AggregationDaily).filter_by(date="2026-01-02", metric="event_type", key="security.ban").one().value == 1
    assert db_session.query(AggregationDaily).filter_by(date="2026-01-02", metric="country", key="US").one().value == 1
    assert db_session.query(AggregationDaily).filter_by(date="2026-01-02", metric="summary", key="bans").one().value == 1
    assert db_session.query(Insight).filter_by(type="security_ban_observed", ip="8.8.8.8").count() == 1


def test_manual_ban_action_id_is_not_part_of_scenario_rollup_key(db_session):
    event = Event(
        event_time=datetime(2026, 1, 2, 3, 4, 5),
        event_type="security.ban.manual",
        plugin="crowdsec",
        data_json={"scenario": "Manual ban via OpenSecDash (action #42)"},
    )

    update_rollups(db_session, event)
    db_session.commit()

    row = db_session.query(AggregationDaily).filter_by(date="2026-01-02", metric="scenario").one()
    assert row.key == "Manual ban via OpenSecDash"
    assert row.value == 1


def test_event_filters_support_taxonomy_wildcards_local_ips_and_boolean_search(db_session):
    disable_geoip(db_session)
    now = datetime(2026, 1, 2, 12, 0, 0)
    rows = [
        Event(event_time=now, timestamp=now, source="test", plugin="traefik_log", event_type="access.allowed", severity="info", ip="10.0.0.5", path="/health", status_code=200),
        Event(event_time=now + timedelta(seconds=1), timestamp=now, source="test", plugin="traefik_log", event_type="access.error", severity="error", ip="8.8.8.8", path="/wp-login.php", status_code=404, country="US"),
        Event(event_time=now + timedelta(seconds=2), timestamp=now, source="test", plugin="crowdsec", event_type="security.geoblock", severity="warning", ip="1.1.1.1", path="/admin", status_code=403, country="-"),
    ]
    db_session.add_all(rows)
    db_session.commit()

    access_events = apply_event_filters(db_session.query(Event), {"event_type": "access.*"}).order_by(Event.id).all()
    assert [event.event_type for event in access_events] == ["access.allowed", "access.error"]

    public_events = apply_event_filters(db_session.query(Event), {"hide_local_ips": True}).order_by(Event.ip).all()
    assert [event.ip for event in public_events] == ["1.1.1.1", "8.8.8.8"]

    no_country_events = apply_event_filters(db_session.query(Event), {"country": "-"}).order_by(Event.id).all()
    assert [event.event_type for event in no_country_events] == ["access.allowed", "security.geoblock"]

    search_result = apply_event_filters(db_session.query(Event), {"q": "wp-login && 404"}).one()
    assert search_result.ip == "8.8.8.8"
    assert tokenize_search_expression('wp-login && (404 || "access denied")') == ["wp-login", "&&", "(", "404", "||", "access denied", ")"]


def test_access_status_taxonomy_maps_to_severity():
    assert classify_access_status(None) == ("access.allowed", "info")
    assert classify_access_status(200) == ("access.allowed", "info")
    assert classify_access_status(403) == ("access.denied", "warning")
    assert classify_access_status(404) == ("access.error", "warning")
    assert classify_access_status(500) == ("access.error", "error")
