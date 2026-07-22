from sqlalchemy import event as sqlalchemy_event

from app.models.assets import Asset
from app.models.events import Event
from app.models.systems import System
import pytest

from app.services.events import MAX_SEARCH_DEPTH, MAX_SEARCH_LENGTH, MAX_SEARCH_TOKENS, apply_event_filters


def _matching_ids(db_session, filters):
    return [event.id for event in apply_event_filters(db_session.query(Event), filters).order_by(Event.id).all()]


def test_event_filters_apply_country_list_not_and_status_range(db_session):
    ru = Event(event_type="access.error", severity="warning", plugin="traefik_log", country="RU", status_code=404)
    cn = Event(event_type="access.error", severity="warning", plugin="traefik_log", country="CN", status_code=500)
    de = Event(event_type="access.error", severity="warning", plugin="traefik_log", country="DE", status_code=403)
    db_session.add_all([ru, cn, de])
    db_session.commit()

    assert _matching_ids(db_session, {"country_in": ["ru", "CN"]}) == [ru.id, cn.id]
    assert _matching_ids(db_session, {"country_not": "de"}) == [ru.id, cn.id]
    assert _matching_ids(db_session, {"status_code_min": 400, "status_code_max": 499}) == [ru.id, de.id]


def test_event_filters_normalize_asn_and_match_hostname_substrings(db_session):
    matching = Event(event_type="access.error", severity="warning", plugin="traefik_log", asn="AS3320", hostname="api.example.test")
    other = Event(event_type="access.error", severity="warning", plugin="traefik_log", asn="AS64500", hostname="web.example.test")
    db_session.add_all([matching, other])
    db_session.commit()

    assert _matching_ids(db_session, {"asn": "3320"}) == [matching.id]
    assert _matching_ids(db_session, {"hostname": "api.example"}) == [matching.id]


def test_event_filters_match_assets_by_id_or_name(db_session):
    system = System(vmid="100", hostname="apps", system_type="vm")
    db_session.add(system)
    db_session.flush()
    nextcloud = Asset(system_id=system.id, name="Nextcloud", hostname="cloud.example.test")
    other_asset = Asset(system_id=system.id, name="Vaultwarden", hostname="vault.example.test")
    db_session.add_all([nextcloud, other_asset])
    db_session.flush()
    matching = Event(event_type="access.error", severity="warning", plugin="traefik_log", asset_id=nextcloud.id)
    other = Event(event_type="access.error", severity="warning", plugin="traefik_log", asset_id=other_asset.id)
    db_session.add_all([matching, other])
    db_session.commit()

    assert _matching_ids(db_session, {"asset": str(nextcloud.id)}) == [matching.id]
    assert _matching_ids(db_session, {"asset": "Nextcloud"}) == [matching.id]
    assert _matching_ids(db_session, {"asset": "cloud.example.test"}) == [matching.id]


def test_event_filters_match_asset_name_with_one_select(db_session):
    system = System(vmid="100", hostname="apps", system_type="vm")
    db_session.add(system)
    db_session.flush()
    asset = Asset(system_id=system.id, name="Nextcloud", hostname="cloud.example.test")
    db_session.add(asset)
    db_session.flush()
    matching = Event(event_type="access.error", severity="warning", plugin="traefik_log", asset_id=asset.id)
    db_session.add(matching)
    db_session.commit()

    select_statements = []

    def record_select(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    sqlalchemy_event.listen(db_session.bind, "before_cursor_execute", record_select)
    try:
        assert _matching_ids(db_session, {"asset": "Nextcloud"}) == [matching.id]
    finally:
        sqlalchemy_event.remove(db_session.bind, "before_cursor_execute", record_select)

    assert len(select_statements) == 1


def test_event_filters_ignore_empty_or_invalid_new_values(db_session):
    first = Event(event_type="access.error", severity="warning", plugin="traefik_log", country="DE", status_code=404)
    second = Event(event_type="access.error", severity="warning", plugin="traefik_log", country="NL", status_code=500)
    db_session.add_all([first, second])
    db_session.commit()

    filters = {
        "country_in": ["invalid"],
        "country_not": "invalid",
        "status_code_min": "not-a-status",
        "status_code_max": None,
        "asn": "invalid",
        "hostname": "",
        "asset": "",
    }

    assert _matching_ids(db_session, filters) == [first.id, second.id]


def test_search_excludes_raw_payloads_unless_explicitly_enabled(db_session):
    structured = Event(event_type="security.marker", severity="warning", plugin="crowdsec", path="/visible")
    raw_only = Event(
        event_type="security.other",
        severity="warning",
        plugin="crowdsec",
        data_json={"detail": "payload-marker"},
        raw_data="raw-marker",
    )
    db_session.add_all([structured, raw_only])
    db_session.commit()

    assert _matching_ids(db_session, {"q": "visible"}) == [structured.id]
    assert _matching_ids(db_session, {"q": "payload-marker"}) == []
    assert _matching_ids(db_session, {"q": "payload-marker", "include_raw_data": True}) == [raw_only.id]
    assert _matching_ids(db_session, {"q": "raw-marker", "include_raw_data": True}) == [raw_only.id]


@pytest.mark.parametrize(
    "query",
    [
        "x" * (MAX_SEARCH_LENGTH + 1),
        " && ".join(["term"] * (MAX_SEARCH_TOKENS // 2 + 2)),
        "(" * (MAX_SEARCH_DEPTH + 1) + "term" + ")" * (MAX_SEARCH_DEPTH + 1),
        "&& term",
        "term ||",
        '""',
        '"unterminated',
    ],
)
def test_search_rejects_oversized_or_empty_expressions(db_session, query):
    with pytest.raises(ValueError):
        apply_event_filters(db_session.query(Event), {"q": query})


def test_search_uses_bound_structured_predicates_for_ip_asn_status_and_country(db_session):
    query_text = str(apply_event_filters(db_session.query(Event), {"q": "198.51.100.10"}))
    assert "events.ip =" in query_text
    assert " LIKE " not in query_text

    assert "events.asn =" in str(apply_event_filters(db_session.query(Event), {"q": "AS64500"}))
    assert "events.status_code =" in str(apply_event_filters(db_session.query(Event), {"q": "404"}))
    assert "events.country =" in str(apply_event_filters(db_session.query(Event), {"q": "DE"}))


def test_whitespace_search_does_not_generate_match_everything_like(db_session):
    query_text = str(apply_event_filters(db_session.query(Event), {"q": "   "}))

    assert " LIKE " not in query_text
