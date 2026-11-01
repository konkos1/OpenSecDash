from datetime import datetime, timedelta

import pytest
from sqlalchemy import event as sql_event

from app.models.core import Insight
from app.services.events import create_rule_based_insights, store_event


START = datetime(2020, 1, 1)


def _event(db, kind, seconds=0, **values):
    return store_event(db, source="test", plugin="test", event_type=kind,
                       event_time=START + timedelta(seconds=seconds),
                       **{"ip": "192.0.2.1", **values})


@pytest.mark.parametrize("kind,insight_type", [
    ("security.geoblock", "blocked_request"), ("security.ban", "ban_after_access"),
    ("security.ban.manual", "ban_after_access"),
])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("status,access_type", [(404, "access.error"), (500, "access.error"), (403, "access.denied")])
def test_relationship_keeps_status_and_pair_in_both_import_orders(db_session, kind, insight_type, reverse, status, access_type):
    inputs = [(access_type, 0, {"status_code": status}), (kind, 10, {})]
    events = [_event(db_session, event_type, seconds, **values)
              for event_type, seconds, values in (reversed(inputs) if reverse else inputs)]
    for item in events:
        create_rule_based_insights(db_session, item)
    row = db_session.query(Insight).filter_by(type=insight_type).one()
    assert row.related_event_ids == sorted(item.id for item in events)
    assert row.dedupe_key == f"{insight_type}:pair:{row.related_event_ids[0]}:{row.related_event_ids[1]}"
    assert row.occurred_at == START + timedelta(seconds=10)
    assert str(status) in row.description and "10" in row.description
    assert status == 404 or "404" not in row.title + row.description
    assert row.title == ("Access failure near GeoBlock" if kind == "security.geoblock" else "Security ban after access failures")
    assert row.confidence == 0.6
    assert "missing" in row.description.lower()
    assert row.evidence["kind"] == "correlation"


@pytest.mark.parametrize("kind,insight_type", [("security.geoblock", "blocked_request"), ("security.ban", "ban_after_access")])
@pytest.mark.parametrize("gap,expected", [(-61, False), (-60, True), (0, True), (60, True), (61, False)])
@pytest.mark.parametrize("reverse", [False, True])
def test_relationship_time_boundaries_and_ban_direction(db_session, kind, insight_type, gap, expected, reverse):
    inputs = [("access.error", 0), (kind, gap)]
    for event_type, seconds in reversed(inputs) if reverse else inputs:
        _event(db_session, event_type, seconds, status_code=500 if event_type == "access.error" else None)
    assert db_session.query(Insight).filter_by(type=insight_type).count() == int(expected and (kind == "security.geoblock" or gap >= 0))


@pytest.mark.parametrize("context,confidence", [
    ({"asset_id": 1}, 0.8), ({"hostname": "app.example.com"}, 0.8),
    ({}, 0.6),
])
def test_matching_context_sets_fixed_confidence(db_session, context, confidence):
    _event(db_session, "access.error", status_code=500, **context)
    _event(db_session, "security.geoblock", 10, **context)
    assert db_session.query(Insight).filter_by(type="blocked_request").one().confidence == confidence


@pytest.mark.parametrize("other", [
    {"ip": "192.0.2.2"}, {"asset_id": 2}, {"hostname": "other.example.com"},
])
def test_conflicting_context_is_excluded(db_session, other):
    _event(db_session, "access.error", asset_id=1, hostname="app.example.com", status_code=500)
    _event(db_session, "security.geoblock", 10, **{"asset_id": 1, "hostname": "app.example.com", **other})
    assert db_session.query(Insight).filter_by(type="blocked_request").count() == 0


def test_best_valid_pair_uses_distance_then_id_and_bounded_index_query(db_session):
    first = _event(db_session, "access.error", -10, hostname="APP.example.com:443", status_code=500)
    _event(db_session, "access.error", 10, hostname="app.example.com", status_code=404)
    _event(db_session, "access.error", 1, hostname="other.example.com", status_code=403)
    plans = []
    connection = db_session.connection()

    def inspect_query(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "FROM events" in statement and "ORDER BY" in statement:
            plans.extend(conn.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).fetchall())

    sql_event.listen(connection, "before_cursor_execute", inspect_query)
    try:
        geo = _event(db_session, "security.geoblock", hostname="app.example.com")
    finally:
        sql_event.remove(connection, "before_cursor_execute", inspect_query)
    row = db_session.query(Insight).filter_by(type="blocked_request").one()
    assert row.related_event_ids == [first.id, geo.id]
    assert row.confidence == 0.8
    assert plans and any("SEARCH events USING INDEX" in str(plan) and "event_time>?" in str(plan) and "event_time<?" in str(plan) for plan in plans)


def test_asn_policy_is_only_a_direct_fact_and_independent_bans_survive(db_session):
    _event(db_session, "access.error", status_code=500)
    _event(db_session, "security.ban.asn_policy", 1)
    assert db_session.query(Insight).filter_by(type="ban_after_access").count() == 0
    for seconds in (2, 3):
        _event(db_session, "security.ban", seconds)
    assert db_session.query(Insight).filter_by(type="security_ban_observed").count() == 2
    assert db_session.query(Insight).filter_by(type="asn_policy_security_ban").one().confidence == 0.95


def test_missing_context_on_one_side_remains_weak(db_session):
    _event(db_session, "access.error", status_code=500, asset_id=1, hostname="app.example.com")
    _event(db_session, "security.ban", 10)
    row = db_session.query(Insight).filter_by(type="ban_after_access").one()
    assert row.confidence == 0.6 and "missing" in row.description


def test_central_host_assignment_supplies_shared_asset(db_session):
    from app.models.assets import Asset

    from app.models.systems import System

    system = System(vmid="example", hostname="example")
    db_session.add(system)
    db_session.flush()
    asset = Asset(system_id=system.id, name="Example", host_url="https://app.example.com")
    db_session.add(asset)
    db_session.flush()
    _event(db_session, "access.denied", status_code=403, hostname="APP.example.com:443")
    _event(db_session, "security.geoblock", 10, asset_id=asset.id)
    row = db_session.query(Insight).filter_by(type="blocked_request").one()
    assert row.confidence == 0.8 and f"asset {asset.id}" in row.description


@pytest.mark.parametrize("language", ["en", "de"])
def test_ip_view_shows_neutral_relationship_and_retained_details(db_session, language):
    import app.main  # noqa: F401 - register plugin templates
    from starlette.requests import Request
    from app.api import pages
    from app.core.i18n import translate
    from app.services.settings import save_setting

    save_setting(db_session, "language", language)
    save_setting(db_session, "plugin.traefik_log.enabled", "true")
    _event(db_session, "access.error", status_code=500)
    _event(db_session, "security.geoblock", 10)
    request = Request({"type": "http", "method": "GET", "path": "/ip/192.0.2.1",
                       "headers": [(b"hx-request", b"true")], "query_string": b"",
                       "scheme": "http", "server": ("testserver", 80)})
    response = pages.ip_explorer_page("192.0.2.1", request, db=db_session)
    html = bytes(response.body).decode()
    assert translate("insight.title.blocked_request", language) in html
    assert translate("insight.description.blocked_request", language) in html
    assert "HTTP 500; 10 seconds apart" in html
    assert "asset/host context missing" in html
