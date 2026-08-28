from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from starlette.requests import Request

from app.core.time import utc_now
from app.models.core import (
    Action,
    AggregationDaily,
    CrowdSecAsnBan,
    CrowdSecAsnBanEnforcement,
    CrowdSecAsnBanException,
    CrowdSecDecision,
    Insight,
)
from app.models.events import Event
from app.models.settings import Setting
from app.plugins.manager import get_plugin_manager
from app.services.actions import create_action
from app.services.events import apply_event_filters, store_event
from app.services.rollups import normalize_rollup_key
from conftest import import_plugin_module

decisions = import_plugin_module("crowdsec", "services.decisions")
lapi = import_plugin_module("crowdsec", "services.lapi")
policies = import_plugin_module("crowdsec", "services.policies")
routes = import_plugin_module("crowdsec", "routes")


class FakeLapi:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.adds: list[dict[str, Any]] = []
        self.deletes: list[str] = []
        self.failed_delete_ids: set[str] = set()
        self.next_id = 1

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(lapi, "lapi_login", lambda url, login, password: "token")
        monkeypatch.setattr(lapi, "lapi_add_ban", self.add_ban)
        monkeypatch.setattr(lapi, "lapi_delete_decision", self.delete_decision)
        monkeypatch.setattr(decisions, "_fetch_decisions_via_lapi", self.fetch)

    def add_ban(
        self,
        url: str,
        token: str,
        ip: str,
        duration: str,
        reason: str,
        scenario: str | None = None,
    ) -> None:
        decision_id = str(self.next_id)
        self.next_id += 1
        item = {
            "id": decision_id,
            "value": ip,
            "scope": "Ip",
            "type": "ban",
            "origin": "opensecdash",
            "scenario": scenario,
            "reason": reason,
            "duration": duration,
            "until": (utc_now() + timedelta(days=7)).isoformat(),
            "raw": {"id": decision_id},
        }
        self.items.append(item)
        self.adds.append(dict(item))

    def delete_decision(self, url: str, token: str, decision_id: str) -> None:
        self.deletes.append(decision_id)
        if decision_id in self.failed_delete_ids:
            raise lapi.LapiError(f"delete failed for {decision_id}")
        self.items = [item for item in self.items if str(item["id"]) != decision_id]

    def fetch(self, db) -> tuple[bool, str, list[dict[str, Any]]]:
        return True, "", [dict(item) for item in self.items]

    def add_foreign(self, ip: str, decision_id: str = "foreign-1") -> None:
        self.items.append(
            {
                "id": decision_id,
                "value": ip,
                "scope": "Ip",
                "type": "ban",
                "origin": "crowdsec",
                "scenario": "crowdsecurity/ssh-bf",
                "reason": "crowdsecurity/ssh-bf",
                "duration": "4h",
                "until": (utc_now() + timedelta(hours=4)).isoformat(),
                "raw": {"id": decision_id},
            }
        )


@pytest.fixture()
def policy_runtime(db_session, monkeypatch):
    db_session.add_all(
        [
            Setting(key="action_dry_run", value="false"),
            Setting(key="plugin.crowdsec.enabled", value="true"),
            Setting(key="plugin.crowdsec.lapi_url", value="http://lapi:8080"),
            Setting(key="plugin.crowdsec.lapi_login", value="opensecdash"),
            Setting(key="plugin.crowdsec.lapi_password", value="pw"),
            Setting(key="plugin.geoip.enabled", value="true"),
        ]
    )
    db_session.commit()
    fake = FakeLapi()
    fake.install(monkeypatch)
    return db_session, fake


def _event(db, ip: str, asn: str, provider: str = "Example Network") -> Event:
    event = Event(
        source="test",
        plugin="traefik_log",
        event_type="access.allowed",
        ip=ip,
        asn=asn,
        asn_organization=provider,
        isp=provider,
        geoip_checked=True,
    )
    db.add(event)
    db.commit()
    return event


def _enable(db, event: Event) -> Action:
    return create_action(
        db,
        "security.asn_ban.enable",
        str(event.asn),
        "asn",
        {"event_id": event.id, "ip": "1.2.3.4", "provider_name": "untrusted"},
        confirmed=True,
    )


def _form_request(path: str, next_url: str = "/events") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": f"next={next_url}".encode(),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_enable_form_route_uses_only_server_event_values(policy_runtime):
    db, fake = policy_runtime
    source = _event(db, "8.8.8.8", "AS15169", "Server Provider")

    response = routes.crowdsec_asn_ban_enable(
        _form_request("/crowdsec/asn-bans/enable"),
        event_id=source.id,
        confirmed=True,
        db=db,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/events"
    assert db.query(CrowdSecAsnBan).one().provider_name == "Server Provider"
    assert db.query(CrowdSecAsnBanEnforcement).one().ip == "8.8.8.8"
    assert len(fake.adds) == 1


def test_enable_form_route_requires_confirmation(policy_runtime):
    db, fake = policy_runtime
    source = _event(db, "8.8.8.8", "AS15169")

    routes.crowdsec_asn_ban_enable(
        _form_request("/crowdsec/asn-bans/enable"),
        event_id=source.id,
        confirmed=False,
        db=db,
    )

    assert db.query(CrowdSecAsnBan).count() == 0
    assert fake.adds == []
    failed = db.query(Action).one()
    assert failed.status == "failed"
    assert failed.result == "Action requires confirmation"


def test_enable_uses_server_event_and_creates_one_exact_ip_decision(policy_runtime):
    db, fake = policy_runtime
    source = _event(db, "8.8.8.8", "AS15169", "Google LLC")
    _event(db, "8.8.4.4", "AS15169", "Google LLC")

    action = _enable(db, source)

    assert action.target == "AS15169"
    assert db.query(CrowdSecAsnBan).one().provider_name == "Google LLC"
    enforcement = db.query(CrowdSecAsnBanEnforcement).one()
    assert enforcement.ip == "8.8.8.8"
    assert enforcement.decision_id == "1"
    assert fake.adds == [
        {
            "id": "1",
            "value": "8.8.8.8",
            "scope": "Ip",
            "type": "ban",
            "origin": "opensecdash",
            "scenario": "opensecdash/manual-permanent-asn-ban/AS15169",
            "reason": "Permanent ASN ban AS15169",
            "duration": "7d",
            "until": fake.adds[0]["until"],
            "raw": {"id": "1"},
        }
    ]
    assert all(item["scope"] == "Ip" and "/" not in item["value"] for item in fake.adds)
    auto_action = db.query(Action).filter_by(action_type="security.ban.asn_policy").one()
    auto_event = db.query(Event).filter_by(event_type="security.ban.asn_policy").one()
    assert auto_action.status == "completed"
    assert auto_event.data_json["manual"] is False
    assert auto_event.data_json["trigger"] == "asn_policy"
    assert auto_event.data_json["scenario_group"] == policies.POLICY_SCENARIO_GROUP
    insight = db.query(Insight).filter_by(type="asn_policy_security_ban").one()
    assert (insight.ip, insight.related_event_ids, insight.confidence) == (
        "8.8.8.8",
        [auto_event.id],
        0.95,
    )
    assert "AS15169" in insight.description
    assert "Google LLC" in insight.description
    assert "7d" in insight.description
    assert db.query(Insight).filter_by(type="manual_security_ban").count() == 0
    scenario_rollup = db.query(AggregationDaily).filter_by(metric="scenario").one()
    assert scenario_rollup.key == policies.POLICY_SCENARIO_GROUP


def test_same_event_active_decision_and_expiry_require_a_new_event(policy_runtime):
    db, fake = policy_runtime
    first = _event(db, "8.8.8.8", "AS15169")
    _enable(db, first)

    policies.process_enriched_event(db, first)
    second = _event(db, "8.8.8.8", "AS15169")
    policies.process_enriched_event(db, second)
    assert len(fake.adds) == 1

    fake.items.clear()
    decisions.sync_crowdsec_decisions(db, force=True)
    policies.process_enriched_event(db, second)
    assert len(fake.adds) == 1

    third = _event(db, "8.8.8.8", "AS15169")
    policies.process_enriched_event(db, third)
    assert len(fake.adds) == 2
    assert db.query(Insight).filter_by(type="asn_policy_security_ban").count() == 2


def test_foreign_active_decision_is_respected_without_becoming_owned(policy_runtime):
    db, fake = policy_runtime
    policy = CrowdSecAsnBan(asn="AS15169", provider_name="Google LLC")
    db.add(policy)
    fake.add_foreign("8.8.8.8")
    decisions.sync_crowdsec_decisions(db, force=True)
    event = _event(db, "8.8.8.8", "AS15169")

    policies.process_enriched_event(db, event)
    policies.process_enriched_event(db, event)

    enforcement = db.query(CrowdSecAsnBanEnforcement).one()
    assert enforcement.last_event_id == event.id
    assert enforcement.decision_id is None
    assert fake.adds == []
    assert db.query(CrowdSecDecision).one().origin == "crowdsec"


def test_reclassification_deletes_only_owned_id_and_retry_stays_id_exact(policy_runtime):
    db, fake = policy_runtime
    first = _event(db, "8.8.8.8", "AS15169")
    _enable(db, first)
    fake.add_foreign("8.8.8.8")
    decisions.sync_crowdsec_decisions(db, force=True)
    enforcement = db.query(CrowdSecAsnBanEnforcement).one()
    owned_id = str(enforcement.decision_id)
    fake.failed_delete_ids.add(owned_id)

    changed = _event(db, "8.8.8.8", "AS64500", "Other Network")
    policies.process_enriched_event(db, changed)

    assert enforcement.release_pending is True
    assert fake.deletes == [owned_id]
    assert any(str(item["id"]) == "foreign-1" for item in fake.items)
    assert db.query(CrowdSecAsnBanException).count() == 0

    db.commit()
    fake.failed_delete_ids.clear()
    policies.retry_pending_policy_work(db)

    assert fake.deletes == [owned_id, owned_id]
    assert enforcement.decision_id is None
    assert enforcement.release_pending is False
    assert enforcement.last_observed_asn == "AS64500"
    assert any(str(item["id"]) == "foreign-1" for item in fake.items)
    event = db.query(Event).filter_by(event_type="security.unban.asn_policy_reclassified").one()
    assert event.data_json["decision_id"] == owned_id
    assert event.data_json["old_asn"] == "AS15169"
    assert event.data_json["new_asn"] == "AS64500"
    assert event.data_json["manual"] is False


def test_move_to_another_blocked_asn_keeps_existing_ownership_until_expiry(policy_runtime):
    db, fake = policy_runtime
    first = _event(db, "8.8.8.8", "AS15169")
    _enable(db, first)
    db.add(CrowdSecAsnBan(asn="AS64500", provider_name="Other Network"))
    db.commit()

    changed = _event(db, "8.8.8.8", "AS64500", "Other Network")
    policies.process_enriched_event(db, changed)

    assert fake.deletes == []
    assert len(fake.adds) == 1
    old = db.query(CrowdSecAsnBan).filter_by(asn="AS15169").one()
    new = db.query(CrowdSecAsnBan).filter_by(asn="AS64500").one()
    assert old.enforcements[0].decision_id == "1"
    assert new.enforcements[0].decision_id is None

    fake.items.clear()
    decisions.sync_crowdsec_decisions(db, force=True)
    later = _event(db, "8.8.8.8", "AS64500", "Other Network")
    policies.process_enriched_event(db, later)
    assert len(fake.adds) == 2
    assert new.enforcements[0].decision_id == "2"


def test_manual_owned_unban_adds_exception_only_after_success(policy_runtime):
    db, fake = policy_runtime
    first = _event(db, "8.8.8.8", "AS15169")
    _enable(db, first)
    enforcement = db.query(CrowdSecAsnBanEnforcement).one()

    action = create_action(
        db,
        "security.unban",
        "8.8.8.8",
        "ip",
        {"decision_id": enforcement.decision_id, "asn_ban_id": 999},
        confirmed=True,
    )

    assert action.status == "completed"
    exception = db.query(CrowdSecAsnBanException).one()
    assert exception.asn_ban_id == enforcement.asn_ban_id
    assert exception.ip == "8.8.8.8"
    assert exception.source_action_id == action.id
    assert enforcement.decision_id is None
    assert db.query(Event).filter_by(event_type="security.asn_ban.exception.added").count() == 1

    matching = _event(db, "8.8.8.8", "AS15169")
    policies.process_enriched_event(db, matching)
    assert len(fake.adds) == 1

    create_action(
        db,
        "security.asn_ban.exception.remove",
        "8.8.8.8",
        "ip",
        {"asn_ban_id": exception.asn_ban_id},
        confirmed=True,
    )
    policies.process_enriched_event(db, matching)
    assert len(fake.adds) == 1
    later = _event(db, "8.8.8.8", "AS15169")
    policies.process_enriched_event(db, later)
    assert len(fake.adds) == 2


def test_foreign_failed_and_simulated_unban_never_add_exception(policy_runtime):
    db, fake = policy_runtime
    policy = CrowdSecAsnBan(asn="AS15169")
    db.add(policy)
    fake.add_foreign("8.8.8.8")
    decisions.sync_crowdsec_decisions(db, force=True)

    create_action(
        db,
        "security.unban",
        "8.8.8.8",
        "ip",
        {"decision_id": "foreign-1"},
        confirmed=True,
    )
    assert db.query(CrowdSecAsnBanException).count() == 0

    fake.add_foreign("8.8.4.4", "foreign-2")
    decisions.sync_crowdsec_decisions(db, force=True)
    fake.failed_delete_ids.add("foreign-2")
    failed = create_action(
        db,
        "security.unban",
        "8.8.4.4",
        "ip",
        {"decision_id": "foreign-2"},
        confirmed=True,
    )
    assert failed.status == "failed"
    assert db.query(CrowdSecAsnBanException).count() == 0

    db.query(Setting).filter_by(key="action_dry_run").one().value = "true"
    simulated = create_action(
        db,
        "security.unban",
        "1.1.1.1",
        "ip",
        {},
        confirmed=True,
    )
    assert simulated.status == "completed"
    assert db.query(CrowdSecAsnBanException).count() == 0


def test_provider_change_audit_and_race_safe_acknowledgement(policy_runtime):
    db, fake = policy_runtime
    policy = CrowdSecAsnBan(asn="AS15169")
    db.add(policy)
    db.commit()

    first = _event(db, "8.8.8.8", "AS15169", "Example Network")
    policies.process_enriched_event(db, first)
    assert policy.provider_name == "Example Network"
    assert policy.provider_review_required is False

    same = _event(db, "8.8.4.4", "AS15169", "  example   NETWORK ")
    policies.process_enriched_event(db, same)
    legal_suffix = _event(db, "1.0.0.1", "AS15169", "Example Network, Inc.")
    policies.process_enriched_event(db, legal_suffix)
    empty = _event(db, "1.1.1.1", "AS15169", "")
    policies.process_enriched_event(db, empty)
    assert db.query(Event).filter_by(event_type="security.asn_ban.provider_changed").count() == 0

    changed_first = _event(db, "9.9.9.9", "AS15169", "Example Transit")
    policies.process_enriched_event(db, changed_first)
    policies.process_enriched_event(db, changed_first)
    changed_second = _event(db, "9.9.9.9", "AS15169", "Example Transit")
    policies.process_enriched_event(db, changed_second)
    assert policy.provider_name == "Example Network"
    assert policy.provider_review_required is False

    changed_third = _event(db, "4.4.4.4", "AS15169", "Example Transit")
    policies.process_enriched_event(db, changed_third)
    assert policy.previous_provider_name == "Example Network"
    assert policy.provider_name == "Example Transit"
    assert policy.provider_review_required is True
    assert policy.provider_candidate_name is None
    assert policy.status == "active"
    assert fake.deletes == []
    assert db.query(Event).filter_by(event_type="security.asn_ban.provider_changed").count() == 1
    changed_at = policy.provider_name_changed_at
    assert changed_at is not None

    while_open = _event(db, "8.8.8.8", "AS15169", "Third Network")
    policies.process_enriched_event(db, while_open)
    assert policy.provider_name == "Example Transit"
    assert db.query(Event).filter_by(event_type="security.asn_ban.provider_changed").count() == 1

    with pytest.raises(ValueError, match="changed again"):
        create_action(
            db,
            "security.asn_ban.provider_change.acknowledge",
            "AS15169",
            "asn",
            {"provider_name_changed_at": "2026-01-01T00:00:00"},
            confirmed=True,
        )

    create_action(
        db,
        "security.asn_ban.provider_change.acknowledge",
        "AS15169",
        "asn",
        {"provider_name_changed_at": changed_at.isoformat()},
        confirmed=True,
    )
    assert policy.provider_review_required is False
    assert policy.provider_reviewed_at is not None
    assert policy.status == "active"
    assert fake.deletes == []

    for ip in ("4.4.4.4", "4.4.4.4", "8.8.8.8"):
        changed_again = _event(db, ip, "AS15169", "Third Network")
        policies.process_enriched_event(db, changed_again)
    assert policy.provider_review_required is True
    assert db.query(Event).filter_by(event_type="security.asn_ban.provider_changed").count() == 2


def test_disable_partial_failure_is_removing_and_retry_deletes_only_owned(policy_runtime):
    db, fake = policy_runtime
    first = _event(db, "8.8.8.8", "AS15169")
    _enable(db, first)
    second = _event(db, "8.8.4.4", "AS15169")
    policies.process_enriched_event(db, second)
    fake.add_foreign("8.8.8.8")
    decisions.sync_crowdsec_decisions(db, force=True)
    own_ids = {
        str(enforcement.decision_id)
        for enforcement in db.query(CrowdSecAsnBanEnforcement).all()
        if enforcement.decision_id
    }
    failing_id = sorted(own_ids)[-1]
    fake.failed_delete_ids.add(failing_id)

    action = create_action(
        db,
        "security.asn_ban.disable",
        "AS15169",
        "asn",
        {},
        confirmed=True,
    )

    policy = db.query(CrowdSecAsnBan).one()
    assert action.status == "failed"
    assert policy.status == "removing"
    assert failing_id in (policy.removal_error or "")
    assert "foreign-1" not in fake.deletes

    fake.failed_delete_ids.clear()
    policies.retry_pending_policy_work(db)
    assert db.query(CrowdSecAsnBan).count() == 0
    assert db.query(CrowdSecAsnBanEnforcement).count() == 0
    assert any(str(item["id"]) == "foreign-1" for item in fake.items)
    assert "foreign-1" not in fake.deletes


def test_disabled_integrations_and_dry_run_do_not_enforce_or_persist(policy_runtime):
    db, fake = policy_runtime
    source = _event(db, "8.8.8.8", "AS15169")
    db.query(Setting).filter_by(key="action_dry_run").one().value = "true"

    simulated = _enable(db, source)
    assert simulated.status == "completed"
    assert simulated.result == "dry-run: action was recorded but not executed"
    assert db.query(CrowdSecAsnBan).count() == 0
    assert db.query(CrowdSecAsnBanEnforcement).count() == 0
    assert fake.adds == []

    db.query(Setting).filter_by(key="action_dry_run").one().value = "false"
    db.query(Setting).filter_by(key="plugin.geoip.enabled").one().value = "false"
    with pytest.raises(ValueError, match="must both be enabled"):
        _enable(db, source)
    assert db.query(CrowdSecAsnBan).count() == 0

    db.query(Setting).filter_by(key="plugin.geoip.enabled").one().value = "true"
    db.query(Setting).filter_by(key="plugin.crowdsec.enabled").one().value = "false"
    with pytest.raises(ValueError, match="must both be enabled"):
        _enable(db, source)
    assert db.query(CrowdSecAsnBan).count() == 0


def test_internal_actions_are_hidden_and_rejected_from_public_entry(policy_runtime):
    db, _fake = policy_runtime
    manager = get_plugin_manager()
    available = {
        definition.action_type
        for _plugin_id, definition in manager.available_actions(db, "ip", "8.8.8.8")
    }
    assert "security.ban.asn_policy" not in available
    assert "security.unban.asn_policy_reclassified" not in available

    with pytest.raises(ValueError, match="internal only"):
        create_action(
            db,
            "security.ban.asn_policy",
            "8.8.8.8",
            "ip",
            {
                "asn_ban_id": 1,
                "asn": "AS15169",
                "trigger": "asn_policy",
                "scenario": policies.policy_scenario("AS15169"),
            },
            confirmed=True,
        )


def test_rollup_label_hook_localizes_group_and_keeps_prefix_filter(policy_runtime):
    db, _fake = policy_runtime
    manager = get_plugin_manager()
    assert manager.rollup_display_label_key("scenario", policies.POLICY_SCENARIO_GROUP) == (
        "crowdsec.scenario.manual_permanent_asn_ban"
    )
    assert manager.rollup_display_label_key(
        "scenario",
        "opensecdash/manual-permanent-asn-ban/AS15169",
    ) is None
    assert normalize_rollup_key(
        "scenario",
        "opensecdash/manual-permanent-asn-ban/AS15169",
    ) == policies.POLICY_SCENARIO_GROUP
    assert normalize_rollup_key(
        "scenario",
        "manual-permanent-asn-ban/AS14618",
    ) == policies.POLICY_SCENARIO_GROUP
    assert normalize_rollup_key(
        "scenario",
        "opensecdash/manual-permanent-asn-ban/not-an-asn",
    ) == "opensecdash/manual-permanent-asn-ban/not-an-asn"

    first = store_event(
        db,
        source="Action Framework",
        plugin="crowdsec",
        event_type="security.ban.asn_policy",
        ip="8.8.8.8",
        data_json={
            "asn": "AS15169",
            "provider_name": "Google LLC",
            "duration": "7d",
            "scenario": "opensecdash/manual-permanent-asn-ban/AS15169",
            "scenario_group": policies.POLICY_SCENARIO_GROUP,
        },
    )
    second = store_event(
        db,
        source="Action Framework",
        plugin="crowdsec",
        event_type="security.ban.asn_policy",
        ip="1.1.1.1",
        data_json={
            "asn": "AS13335",
            "provider_name": "Cloudflare",
            "duration": "7d",
            "scenario": "opensecdash/manual-permanent-asn-ban/AS13335",
            "scenario_group": policies.POLICY_SCENARIO_GROUP,
        },
    )
    assert (first.data_json or {})["scenario"] != (second.data_json or {})["scenario"]
    rollup = db.query(AggregationDaily).filter_by(metric="scenario").one()
    assert rollup.key == policies.POLICY_SCENARIO_GROUP
    assert rollup.value == 2
    drilldown = apply_event_filters(
        db.query(Event),
        {"q": policies.POLICY_SCENARIO_GROUP, "include_raw_data": True},
    ).all()
    assert {event.id for event in drilldown} == {first.id, second.id}


def test_asn_specific_scenario_without_group_uses_stable_rollup_key(policy_runtime):
    db, _fake = policy_runtime

    event = store_event(
        db,
        source="CrowdSec Log",
        plugin="crowdsec",
        event_type="security.ban",
        ip="3.5.140.1",
        data_json={"scenario": policies.policy_scenario("AS14618"), "duration": "7d"},
        raw_data="standalone CrowdSec ASN scenario regression event",
    )

    assert (event.data_json or {})["scenario"] == policies.policy_scenario("AS14618")
    rollup = db.query(AggregationDaily).filter_by(metric="scenario").one()
    assert rollup.key == policies.POLICY_SCENARIO_GROUP


def test_unquoted_crowdsec_log_keeps_complete_asn_policy_scenario(policy_runtime):
    _db, _fake = policy_runtime
    plugin: Any = get_plugin_manager().plugins["crowdsec"]

    parsed = plugin.parse_log_line(
        'time="2026-08-28T10:00:00Z" level=info '
        'msg="(machine/opensecdash) opensecdash/manual-permanent-asn-ban/AS14618 '
        'by ip 3.5.140.1 : 7d ban on Ip 3.5.140.1"'
    )

    assert parsed is not None
    assert parsed["data_json"]["scenario"] == policies.policy_scenario("AS14618")


def test_ambiguous_policy_decision_is_not_claimed_or_reported_as_success(policy_runtime, monkeypatch):
    db, fake = policy_runtime
    original_add = fake.add_ban

    def add_twice(*args, **kwargs) -> None:
        original_add(*args, **kwargs)
        original_add(*args, **kwargs)

    monkeypatch.setattr(lapi, "lapi_add_ban", add_twice)
    source = _event(db, "8.8.8.8", "AS15169")

    _enable(db, source)

    action = db.query(Action).filter_by(action_type="security.ban.asn_policy").one()
    enforcement = db.query(CrowdSecAsnBanEnforcement).one()
    assert action.status == "failed"
    assert "2 candidates" in (action.result or "")
    assert enforcement.decision_id is None
    assert db.query(Event).filter_by(event_type="security.ban.asn_policy").count() == 0
    assert db.query(Insight).filter_by(type="asn_policy_security_ban").count() == 0


def test_reclassified_ip_returns_to_old_policy_only_on_new_event(policy_runtime):
    db, fake = policy_runtime
    first = _event(db, "8.8.8.8", "AS15169")
    _enable(db, first)
    changed = _event(db, "8.8.8.8", "AS64500")
    policies.process_enriched_event(db, changed)
    db.commit()
    assert len(fake.adds) == 1
    assert fake.items == []

    policies.process_enriched_event(db, first)
    assert len(fake.adds) == 1
    returned = _event(db, "8.8.8.8", "AS15169")
    policies.process_enriched_event(db, returned)
    assert len(fake.adds) == 2


def test_existing_policy_is_paused_without_simulated_auto_actions(policy_runtime):
    db, fake = policy_runtime
    policy = CrowdSecAsnBan(asn="AS15169")
    db.add(policy)
    db.query(Setting).filter_by(key="action_dry_run").one().value = "true"
    db.commit()
    event = _event(db, "8.8.8.8", "AS15169")

    policies.process_enriched_event(db, event)

    assert fake.adds == []
    assert db.query(Action).filter_by(action_type="security.ban.asn_policy").count() == 0
    assert db.query(CrowdSecAsnBanEnforcement).count() == 0


def test_auto_ban_counts_widgets_history_and_later_log_import_once(policy_runtime):
    db, fake = policy_runtime
    source = _event(db, "8.8.8.8", "AS15169")
    _enable(db, source)
    auto_event = db.query(Event).filter_by(event_type="security.ban.asn_policy").one()
    plugin = get_plugin_manager().plugins["crowdsec"]

    assert plugin.ip_page_count_widgets(db, "8.8.8.8")[0]["value"] == 1
    widgets = {widget.id: widget for widget in plugin.dashboard_widgets(db)}
    assert widgets["crowdsec.active_bans"].value == 1
    scenario_row = widgets["crowdsec.top_scenarios"].rows[0]
    assert scenario_row["value"] == 1
    assert scenario_row["label_key"] == "crowdsec.scenario.manual_permanent_asn_ban"
    assert "q=opensecdash%2Fmanual-permanent-asn-ban" in scenario_row["href"]
    assert db.query(CrowdSecDecision).filter_by(decision_id="1").count() == 1

    imported = store_event(
        db,
        event_time=auto_event.event_time,
        source="CrowdSec Log",
        source_id="crowdsec-log",
        plugin="crowdsec",
        event_type="security.ban",
        severity="warning",
        ip="8.8.8.8",
        data_json={
            "scenario": "opensecdash/manual-permanent-asn-ban/AS15169",
            "duration": "7d",
        },
        raw_data="7d ban on Ip 8.8.8.8 scenario=opensecdash/manual-permanent-asn-ban/AS15169",
    )
    assert imported.id == auto_event.id
    assert getattr(imported, "_opensecdash_created") is False
    assert db.query(Event).filter(Event.ip == "8.8.8.8", Event.event_type.startswith("security.ban")).count() == 1
    assert db.query(AggregationDaily).filter_by(metric="summary", key="bans").one().value == 1


def test_rollup_label_is_available_in_both_languages(policy_runtime):
    from app.core.i18n import translate

    _db, _fake = policy_runtime
    key = "crowdsec.scenario.manual_permanent_asn_ban"
    assert translate(key, "en") == "Manual permanent ASN ban"
    assert translate(key, "de") == "Manueller dauerhafter ASN-Ban"
