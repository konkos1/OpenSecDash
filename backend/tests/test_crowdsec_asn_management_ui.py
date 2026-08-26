from datetime import datetime
from types import SimpleNamespace

from starlette.requests import Request

import app.main  # noqa: F401 - registers plugin templates
from app.api import pages
from app.models.core import (
    CrowdSecAsnBan,
    CrowdSecAsnBanEnforcement,
    CrowdSecAsnBanException,
    CrowdSecDecision,
    Diagnostic,
    Insight,
)
from app.models.events import Event
from app.models.settings import Setting
from conftest import import_plugin_module

crowdsec_routes = import_plugin_module("crowdsec", "routes")
traefik_routes = import_plugin_module("traefik_log", "routes")
crowdsec_locales = import_plugin_module("crowdsec", "locales")


def _request(path: str, *, hx: bool = False, role: str | None = None) -> Request:
    headers = [(b"hx-request", b"true")] if hx else []
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": headers,
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    if role:
        request.state.user = SimpleNamespace(id=999999, role=role)
    return request


def _html(response) -> str:
    return bytes(response.body).decode()


def _enable_popup_prerequisites(db) -> None:
    db.add_all(
        [
            Setting(key="action_dry_run", value="false"),
            Setting(key="plugin.crowdsec.enabled", value="true"),
            Setting(key="plugin.crowdsec.lapi_url", value="http://lapi:8080"),
            Setting(key="plugin.crowdsec.lapi_login", value="opensecdash"),
            Setting(key="plugin.crowdsec.lapi_password", value="pw"),
            Setting(key="plugin.geoip.enabled", value="true"),
            Setting(key="plugin.traefik_log.enabled", value="true"),
            Diagnostic(plugin="crowdsec", component="lapi", status="healthy"),
            Diagnostic(plugin="geoip", component="plugin", status="healthy"),
        ]
    )
    db.commit()


def test_events_and_access_render_escaped_asn_popup_without_changing_columns(db_session):
    _enable_popup_prerequisites(db_session)
    db_session.add_all(
        [
            Setting(key="ui.events.visible_columns", value="time,asn,isp"),
            Setting(key="ui.access.visible_columns", value="time,asn,isp"),
        ]
    )
    event = Event(
        event_time=datetime(2026, 8, 26, 12),
        event_type="access.allowed",
        plugin="traefik_log",
        ip="8.8.8.8",
        asn="15169",
        asn_organization='<img src=x onerror="alert(1)">',
        isp='<img src=x onerror="alert(1)">',
        geoip_checked=True,
    )
    db_session.add(event)
    db_session.commit()

    events_html = _html(pages.events_page(_request("/events"), range="all", db=db_session))
    access_html = _html(traefik_routes.access_page(_request("/access"), range="all", db=db_session))

    for html in (events_html, access_html):
        assert "data-asn-popup" in html
        assert 'data-asn="AS15169"' in html
        assert 'data-provider="&lt;img src=x onerror=&#34;alert(1)&#34;&gt;"' in html
        assert "<img src=x" not in html
        assert f'data-event-id="{event.id}"' in html
        assert "Select columns" in html
    assert db_session.query(Setting).filter_by(key="ui.events.visible_columns").one().value == "time,asn,isp"
    assert db_session.query(Setting).filter_by(key="ui.access.visible_columns").one().value == "time,asn,isp"


def test_viewer_popup_has_no_mutation_and_policy_states_are_server_declared(db_session):
    _enable_popup_prerequisites(db_session)
    db_session.add(Setting(key="ui.events.visible_columns", value="time,asn"))
    event = Event(
        event_type="access.allowed",
        plugin="traefik_log",
        ip="8.8.8.8",
        asn="AS15169",
        asn_organization="Example Provider",
        isp="Example Provider",
        geoip_checked=True,
    )
    db_session.add(event)
    db_session.commit()

    viewer_html = _html(pages.events_page(_request("/events", role="viewer"), range="all", db=db_session))
    assert "data-asn-popup" in viewer_html
    assert "AS15169 · Example Provider" in viewer_html
    assert "data-enable-url" not in viewer_html

    policy = CrowdSecAsnBan(
        asn="AS15169",
        provider_name="Changed Provider",
        previous_provider_name="Example Provider",
        provider_review_required=True,
        provider_name_changed_at=datetime(2026, 8, 26, 10),
        status="removing",
    )
    db_session.add(policy)
    db_session.commit()
    operator_html = _html(pages.events_page(_request("/events", role="operator"), range="all", db=db_session))
    assert 'data-policy-status="Removal pending"' in operator_html
    assert 'data-provider-review="ASN organization changed – please review"' in operator_html
    assert "data-enable-url" not in operator_html


def test_crowdsec_policy_management_is_deferred_and_shows_owned_state(db_session):
    _enable_popup_prerequisites(db_session)
    policy = CrowdSecAsnBan(
        asn="AS15169",
        provider_name="New Provider",
        previous_provider_name="Old Provider",
        provider_review_required=True,
        provider_name_changed_at=datetime(2026, 8, 26, 10),
        last_matched_at=datetime(2026, 8, 26, 11),
    )
    db_session.add(policy)
    db_session.flush()
    db_session.add_all(
        [
            CrowdSecAsnBanException(asn_ban_id=policy.id, ip="1.1.1.1", source_action_id=42),
            CrowdSecAsnBanEnforcement(
                asn_ban_id=policy.id,
                ip="8.8.8.8",
                decision_id="owned-1",
                scenario="opensecdash/manual-permanent-asn-ban/AS15169",
            ),
            CrowdSecAsnBanEnforcement(
                asn_ban_id=policy.id,
                ip="9.9.9.9",
                decision_id="owned-2",
                release_pending=True,
                release_error="safe retry error",
            ),
            CrowdSecDecision(
                decision_id="owned-1",
                ip="8.8.8.8",
                scope="Ip",
                decision_type="ban",
                origin="opensecdash",
                scenario="opensecdash/manual-permanent-asn-ban/AS15169",
            ),
            Event(
                event_time=datetime(2026, 8, 26, 12),
                event_type="security.ban.asn_policy",
                plugin="crowdsec",
                ip="8.8.8.8",
                data_json={
                    "scenario": "opensecdash/manual-permanent-asn-ban/AS15169",
                    "duration": "7d",
                },
            ),
        ]
    )
    db_session.commit()

    shell = _html(crowdsec_routes.crowdsec_page(_request("/crowdsec"), db=db_session))
    data = _html(crowdsec_routes.crowdsec_page(_request("/crowdsec", hx=True), db=db_session))
    viewer_data = _html(
        crowdsec_routes.crowdsec_page(_request("/crowdsec", hx=True, role="viewer"), db=db_session)
    )

    assert "AS15169" not in shell
    assert "AS15169" in data
    assert "New Provider" in data and "Old Provider" in data
    assert "ASN organization changed – please review" in data
    assert "8.8.8.8" in data and "1.1.1.1" in data
    assert "owned-2" in data and "safe retry error" in data
    assert f'data-refresh-state="crowdsec-asn-policy-{policy.id}-active-decisions"' in data
    assert f'data-refresh-state="crowdsec-asn-policy-{policy.id}-exceptions"' in data
    assert "/exceptions/" in data
    assert "/provider-change/acknowledge" in data
    assert "Only the displayed policy-owned decision ID is retried" in data
    assert "because of the permanent ban for AS15169" in data
    assert "/exceptions/" not in viewer_data
    assert "/provider-change/acknowledge" not in viewer_data
    assert f"/asn-bans/{policy.id}/disable" not in viewer_data


def test_policy_owned_unban_and_asn_insight_use_specific_historical_text(db_session):
    _enable_popup_prerequisites(db_session)
    policy = CrowdSecAsnBan(asn="AS15169", provider_name="Example Provider")
    db_session.add(policy)
    db_session.flush()
    db_session.add_all(
        [
            CrowdSecAsnBanEnforcement(
                asn_ban_id=policy.id,
                ip="8.8.8.8",
                decision_id="owned-1",
                scenario="opensecdash/manual-permanent-asn-ban/AS15169",
            ),
            CrowdSecDecision(
                decision_id="owned-1",
                ip="8.8.8.8",
                scope="Ip",
                decision_type="ban",
                origin="opensecdash",
                scenario="opensecdash/manual-permanent-asn-ban/AS15169",
            ),
            Insight(
                type="asn_policy_security_ban",
                confidence=0.95,
                level="high",
                title="Banned by permanent ASN ban",
                description="AS15169 · Example Provider · 7d",
                ip="8.8.8.8",
            ),
        ]
    )
    db_session.commit()

    shell = _html(pages.ip_explorer_page("8.8.8.8", _request("/ip/8.8.8.8"), db=db_session))
    data = _html(pages.ip_explorer_page("8.8.8.8", _request("/ip/8.8.8.8", hx=True), db=db_session))

    assert "because of the permanent ban for AS15169" in shell
    assert "Banned by permanent ASN ban" in data
    assert "historical finding, not the current decision status" in data
    assert "AS15169 · Example Provider · 7d" in data


def test_german_management_core_texts_are_localized(db_session):
    _enable_popup_prerequisites(db_session)
    db_session.add(Setting(key="language", value="de"))
    db_session.commit()

    html = _html(crowdsec_routes.crowdsec_page(_request("/crowdsec", hx=True), db=db_session))

    assert "Dauerhaft gesperrte ASNs" in html
    assert "Keine dauerhaften ASN-Sperren" in html
    assert "Spalten auswählen" in html


def test_policy_prerequisite_failures_are_visible(db_session):
    db_session.add_all(
        [
            Setting(key="action_dry_run", value="true"),
            Setting(key="plugin.crowdsec.enabled", value="false"),
            Setting(key="plugin.geoip.enabled", value="false"),
        ]
    )
    db_session.commit()

    disabled_html = _html(
        crowdsec_routes.crowdsec_page(_request("/crowdsec", hx=True), db=db_session)
    )
    assert "Action simulation is active" in disabled_html
    assert "GeoIP is disabled" in disabled_html
    assert "CrowdSec is disabled" in disabled_html

    for key, value in (
        ("action_dry_run", "false"),
        ("plugin.crowdsec.enabled", "true"),
        ("plugin.geoip.enabled", "true"),
    ):
        db_session.query(Setting).filter_by(key=key).one().value = value
    db_session.add_all(
        [
            Diagnostic(plugin="crowdsec", component="lapi", status="error", last_error="offline"),
            Diagnostic(plugin="geoip", component="plugin", status="error", last_error="offline"),
        ]
    )
    db_session.commit()

    error_html = _html(
        crowdsec_routes.crowdsec_page(_request("/crowdsec", hx=True), db=db_session)
    )
    assert "GeoIP reports an error" in error_html
    assert "CrowdSec LAPI is not ready" in error_html


def test_foreign_unban_keeps_general_confirmation(db_session):
    _enable_popup_prerequisites(db_session)
    db_session.add(
        CrowdSecDecision(
            decision_id="foreign-1",
            ip="8.8.4.4",
            scope="Ip",
            decision_type="ban",
            origin="crowdsec",
            scenario="crowdsecurity/ssh-bf",
        )
    )
    db_session.commit()

    html = _html(pages.ip_explorer_page("8.8.4.4", _request("/ip/8.8.4.4"), db=db_session))

    assert 'Run &#34;Unban IP&#34; for 8.8.4.4?' in html
    assert "because of the permanent ban" not in html


def test_crowdsec_locale_keys_have_english_german_parity():
    assert set(crowdsec_locales.LOCALES["en"]) == set(crowdsec_locales.LOCALES["de"])
