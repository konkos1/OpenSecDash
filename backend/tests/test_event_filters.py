from app.models.assets import Asset
from app.models.events import Event
from app.models.systems import System
from app.services.events import apply_event_filters


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
