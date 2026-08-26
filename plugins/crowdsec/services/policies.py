from __future__ import annotations

import ipaddress
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.models.core import (
    Action,
    CrowdSecAsnBan,
    CrowdSecAsnBanEnforcement,
    CrowdSecAsnBanException,
    CrowdSecDecision,
)
from app.models.events import Event
from app.services.actions import _create_internal_action
from app.services.events import store_event

ASN_MAX = 4_294_967_295
POLICY_DURATION = "7d"
POLICY_SCENARIO_GROUP = "opensecdash/manual-permanent-asn-ban"
POLICY_SCENARIO_PREFIX = f"{POLICY_SCENARIO_GROUP}/"


def normalize_asn(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("AS"):
        text = text[2:]
    if not text.isdigit():
        raise ValueError("ASN must use AS followed by a positive decimal number")
    number = int(text)
    if number <= 0 or number > ASN_MAX:
        raise ValueError("ASN number must be between 1 and 4294967295")
    return f"AS{number}"


def normalize_global_ip(value: object) -> str:
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("A valid global IP address is required") from exc
    if not address.is_global or address.is_multicast:
        raise ValueError("A global IP address is required")
    return str(address)


def policy_scenario(asn: str) -> str:
    return f"{POLICY_SCENARIO_PREFIX}{normalize_asn(asn)}"


def _actions_simulated(db: Session) -> bool:
    return get_setting_value(db, "action_dry_run", "true").lower() == "true"


def _plugin_enabled(db: Session, plugin_id: str) -> bool:
    return get_setting_value(db, f"plugin.{plugin_id}.enabled", "false").lower() == "true"


def _lapi_configuration_ready(db: Session) -> bool:
    from .lapi import LapiError, validate_lapi_url

    if not get_setting_value(db, "plugin.crowdsec.lapi_login", "").strip():
        return False
    if not get_setting_value(db, "plugin.crowdsec.lapi_password", "").strip():
        return False
    try:
        validate_lapi_url(get_setting_value(db, "plugin.crowdsec.lapi_url", ""))
    except LapiError:
        return False
    return True


def _event_for_enable(db: Session, target: str, parameters: dict[str, Any]) -> tuple[str, Event]:
    asn = normalize_asn(target)
    try:
        event_id = int(str(parameters.get("event_id") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("A valid source event is required for an ASN ban") from exc
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None or not event.geoip_checked or not event.ip or not event.asn:
        raise ValueError("The source event has no completed GeoIP ASN classification")
    if normalize_asn(event.asn) != asn:
        raise ValueError("The source event ASN does not match the requested ASN ban")
    normalize_global_ip(event.ip)
    return asn, event


def validate_action_parameters(
    db: Session,
    action_type: str,
    target: str,
    parameters: dict[str, Any],
    dry_run: bool,
) -> tuple[str, dict[str, Any]]:
    if action_type == "security.asn_ban.enable":
        asn, event = _event_for_enable(db, target, parameters)
        if not _plugin_enabled(db, "crowdsec") or not _plugin_enabled(db, "geoip"):
            raise ValueError("CrowdSec and GeoIP must both be enabled for permanent ASN bans")
        if not dry_run and not _lapi_configuration_ready(db):
            raise ValueError("CrowdSec LAPI must be configured for permanent ASN bans")
        existing = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.asn == asn).first()
        if existing is not None:
            if existing.status == "active":
                raise ValueError(f"Permanent ASN ban already active: {asn}")
            raise ValueError(f"Permanent ASN ban is currently being removed: {asn}")
        return asn, {
            "event_id": event.id,
            "ip": normalize_global_ip(event.ip),
            "asn": asn,
            "provider_name": str(event.isp or "").strip()[:255] or None,
        }

    if action_type == "security.asn_ban.disable":
        asn = normalize_asn(target)
        policy = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.asn == asn).first()
        if policy is None:
            raise ValueError(f"Permanent ASN ban not found: {asn}")
        return asn, {"asn": asn, "asn_ban_id": policy.id}

    if action_type == "security.asn_ban.exception.remove":
        ip = normalize_global_ip(target)
        try:
            asn_ban_id = int(str(parameters.get("asn_ban_id") or ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("A valid ASN ban is required for this exception") from exc
        policy = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.id == asn_ban_id).first()
        if policy is None:
            raise ValueError("Permanent ASN ban not found for this exception")
        exception = (
            db.query(CrowdSecAsnBanException)
            .filter(
                CrowdSecAsnBanException.asn_ban_id == policy.id,
                CrowdSecAsnBanException.ip == ip,
            )
            .first()
        )
        return ip, {
            "asn": policy.asn,
            "asn_ban_id": policy.id,
            "exception_id": exception.id if exception is not None else None,
        }

    if action_type == "security.asn_ban.provider_change.acknowledge":
        asn = normalize_asn(target)
        policy = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.asn == asn).first()
        if policy is None or not policy.provider_review_required or policy.provider_name_changed_at is None:
            raise ValueError("No current provider change requires acknowledgement")
        expected = str(parameters.get("provider_name_changed_at") or "").strip()
        if expected != policy.provider_name_changed_at.isoformat():
            raise ValueError("Provider information changed again; review the current value before acknowledging")
        return asn, {
            "asn": asn,
            "asn_ban_id": policy.id,
            "provider_name_changed_at": expected,
        }

    if action_type == "security.ban.asn_policy":
        ip = normalize_global_ip(target)
        policy = _validated_internal_policy(db, parameters, require_active=True)
        if normalize_asn(parameters.get("asn")) != policy.asn:
            raise ValueError("Internal ASN policy context does not match the policy")
        from .decisions import active_decision_for_ip

        if active_decision_for_ip(db, ip) is not None:
            raise ValueError("An active CrowdSec ban decision already exists for this IP")
        return ip, {
            **parameters,
            "asn": policy.asn,
            "scenario": policy_scenario(policy.asn),
            "scenario_group": POLICY_SCENARIO_GROUP,
            "duration": POLICY_DURATION,
            "manual": False,
            "trigger": "asn_policy",
        }

    if action_type == "security.unban.asn_policy_reclassified":
        ip = normalize_global_ip(target)
        policy = _validated_internal_policy(db, parameters, require_active=False)
        enforcement = _validated_enforcement(db, policy, ip, parameters)
        decision_id = str(parameters.get("decision_id") or "").strip()
        if not decision_id or enforcement.decision_id != decision_id:
            raise ValueError("Internal reclassification decision id does not match policy ownership")
        return ip, {
            **parameters,
            "old_asn": policy.asn,
            "new_asn": normalize_asn(parameters.get("new_asn")),
            "decision_id": decision_id,
            "manual": False,
            "trigger": "asn_policy_reclassified",
        }

    return target, parameters


def _validated_internal_policy(
    db: Session,
    parameters: dict[str, Any],
    *,
    require_active: bool,
) -> CrowdSecAsnBan:
    try:
        asn_ban_id = int(str(parameters.get("asn_ban_id") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Missing server-controlled ASN policy id") from exc
    policy = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.id == asn_ban_id).first()
    if policy is None or (require_active and policy.status != "active"):
        raise ValueError("Server-controlled ASN policy is not active")
    return policy


def _validated_enforcement(
    db: Session,
    policy: CrowdSecAsnBan,
    ip: str,
    parameters: dict[str, Any],
) -> CrowdSecAsnBanEnforcement:
    try:
        enforcement_id = int(str(parameters.get("enforcement_id") or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Missing server-controlled ASN enforcement id") from exc
    enforcement = (
        db.query(CrowdSecAsnBanEnforcement)
        .filter(
            CrowdSecAsnBanEnforcement.id == enforcement_id,
            CrowdSecAsnBanEnforcement.asn_ban_id == policy.id,
            CrowdSecAsnBanEnforcement.ip == ip,
        )
        .first()
    )
    if enforcement is None:
        raise ValueError("Server-controlled ASN enforcement does not match")
    return enforcement


def enable_policy(db: Session, target: str, parameters: dict[str, Any]) -> str:
    asn = normalize_asn(target)
    policy = CrowdSecAsnBan(
        asn=asn,
        provider_name=str(parameters.get("provider_name") or "").strip()[:255] or None,
        status="active",
    )
    db.add(policy)
    db.flush()
    event = db.query(Event).filter(Event.id == int(parameters["event_id"])).one()
    process_enriched_event(db, event)
    return f"Permanent ASN ban enabled for {asn}"


def disable_policy(db: Session, target: str) -> str:
    asn = normalize_asn(target)
    policy = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.asn == asn).one()
    policy.status = "removing"
    policy.removal_error = None
    db.flush()
    if not _finish_policy_removal(db, policy):
        raise RuntimeError(policy.removal_error or f"Permanent ASN ban removal incomplete: {asn}")
    return f"Permanent ASN ban disabled for {asn}"


def remove_exception(db: Session, target: str, parameters: dict[str, Any]) -> str:
    ip = normalize_global_ip(target)
    exception_id = parameters.get("exception_id")
    if exception_id is None:
        return f"ASN policy exception already absent for {ip}"
    exception = db.query(CrowdSecAsnBanException).filter(CrowdSecAsnBanException.id == int(exception_id)).first()
    if exception is not None:
        policy = exception.asn_ban
        db.delete(exception)
        store_event(
            db,
            source="Action Framework",
            source_id="actions",
            plugin="crowdsec",
            plugin_id="crowdsec",
            event_type="security.asn_ban.exception.removed",
            severity="info",
            ip=ip,
            data_json={"asn": policy.asn, "asn_ban_id": policy.id, "ip": ip},
        )
    return f"ASN policy exception removed for {ip}"


def acknowledge_provider_change(db: Session, target: str, parameters: dict[str, Any]) -> str:
    policy = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.asn == normalize_asn(target)).one()
    expected = str(parameters.get("provider_name_changed_at") or "")
    if policy.provider_name_changed_at is None or policy.provider_name_changed_at.isoformat() != expected:
        raise ValueError("Provider information changed again; review the current value before acknowledging")
    policy.provider_review_required = False
    policy.provider_reviewed_at = utc_now().replace(tzinfo=None)
    store_event(
        db,
        source="Action Framework",
        source_id="actions",
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.asn_ban.provider_change.acknowledged",
        severity="info",
        data_json={
            "asn": policy.asn,
            "asn_ban_id": policy.id,
            "provider_name": policy.provider_name,
            "provider_name_changed_at": expected,
        },
    )
    return f"Provider change acknowledged for {policy.asn}"


def process_enriched_event(db: Session, event: Event) -> None:
    if not event.geoip_checked or not event.ip or not event.asn:
        return
    try:
        ip = normalize_global_ip(event.ip)
        asn = normalize_asn(event.asn)
    except ValueError:
        return

    current_policy = (
        db.query(CrowdSecAsnBan)
        .filter(CrowdSecAsnBan.asn == asn, CrowdSecAsnBan.status == "active")
        .first()
    )
    if current_policy is not None:
        _record_policy_match(db, current_policy, event)

    if (
        _actions_simulated(db)
        or not _plugin_enabled(db, "crowdsec")
        or not _plugin_enabled(db, "geoip")
        or not _lapi_configuration_ready(db)
    ):
        return

    _release_reclassified_enforcements(db, ip, asn, current_policy)
    if current_policy is None:
        return
    enforcement = (
        db.query(CrowdSecAsnBanEnforcement)
        .filter(
            CrowdSecAsnBanEnforcement.asn_ban_id == current_policy.id,
            CrowdSecAsnBanEnforcement.ip == ip,
        )
        .first()
    )
    if enforcement is not None and enforcement.last_event_id == event.id:
        return
    if enforcement is None:
        enforcement = CrowdSecAsnBanEnforcement(asn_ban_id=current_policy.id, ip=ip)
        db.add(enforcement)
        db.flush()
    enforcement.last_event_id = event.id
    enforcement.last_seen_at = event.event_time
    enforcement.last_observed_asn = asn
    if (
        db.query(CrowdSecAsnBanException)
        .filter(
            CrowdSecAsnBanException.asn_ban_id == current_policy.id,
            CrowdSecAsnBanException.ip == ip,
        )
        .first()
        is not None
    ):
        return

    from .decisions import active_decision_for_ip

    if active_decision_for_ip(db, ip) is not None:
        return
    action = _create_internal_action(
        db,
        "security.ban.asn_policy",
        ip,
        {
            "asn": asn,
            "event_id": event.id,
            "enforcement_id": enforcement.id,
            "provider_name": current_policy.provider_name,
            "scenario": policy_scenario(asn),
            "scenario_group": POLICY_SCENARIO_GROUP,
            "duration": POLICY_DURATION,
            "reason": f"Permanent ASN ban {asn}",
        },
        trigger="asn_policy",
        asn_ban_id=current_policy.id,
    )
    enforcement.action_id = action.id


def _record_policy_match(db: Session, policy: CrowdSecAsnBan, event: Event) -> None:
    policy.last_matched_at = event.event_time
    provider_name = str(event.isp or "").strip()[:255]
    if not provider_name:
        return
    if not policy.provider_name:
        policy.provider_name = provider_name
        return
    if _provider_comparison_value(policy.provider_name) == _provider_comparison_value(provider_name):
        return
    previous = policy.provider_name
    changed_at = utc_now().replace(tzinfo=None)
    policy.previous_provider_name = previous
    policy.provider_name = provider_name
    policy.provider_name_changed_at = changed_at
    policy.provider_review_required = True
    store_event(
        db,
        source="GeoIP enrichment",
        source_id="geoip",
        plugin="crowdsec",
        plugin_id="crowdsec",
        event_type="security.asn_ban.provider_changed",
        severity="warning",
        data_json={
            "asn": policy.asn,
            "asn_ban_id": policy.id,
            "previous_provider_name": previous,
            "provider_name": provider_name,
            "provider_name_changed_at": changed_at.isoformat(),
        },
    )


def _provider_comparison_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _release_reclassified_enforcements(
    db: Session,
    ip: str,
    new_asn: str,
    new_policy: CrowdSecAsnBan | None,
) -> None:
    enforcements = (
        db.query(CrowdSecAsnBanEnforcement)
        .join(CrowdSecAsnBan)
        .filter(
            CrowdSecAsnBanEnforcement.ip == ip,
            CrowdSecAsnBanEnforcement.decision_id.isnot(None),
            CrowdSecAsnBan.asn != new_asn,
        )
        .all()
    )
    for enforcement in enforcements:
        enforcement.last_observed_asn = new_asn
        if new_policy is not None:
            # Keep the already-owned decision until it expires. A later event
            # can create an enforcement for the new blocked ASN after sync has
            # observed that expiry; ownership is never transferred in place.
            continue
        if enforcement.release_pending:
            continue
        _run_reclassification_release(db, enforcement, new_asn)


def _run_reclassification_release(
    db: Session,
    enforcement: CrowdSecAsnBanEnforcement,
    new_asn: str,
) -> None:
    policy = enforcement.asn_ban
    decision_id = str(enforcement.decision_id or "").strip()
    if not decision_id:
        enforcement.release_error = "Owned decision id is unavailable; no broad IP release was attempted"
        return
    action = _create_internal_action(
        db,
        "security.unban.asn_policy_reclassified",
        enforcement.ip,
        {
            "old_asn": policy.asn,
            "new_asn": new_asn,
            "enforcement_id": enforcement.id,
            "decision_id": decision_id,
            "scenario": enforcement.scenario,
        },
        trigger="asn_policy_reclassified",
        asn_ban_id=policy.id,
    )
    enforcement.action_id = action.id
    if action.status != "completed":
        enforcement.release_pending = True
        enforcement.release_error = action.result or "CrowdSec decision release failed"


def finalize_action(db: Session, action: Action) -> None:
    if action.action_type == "security.ban.asn_policy":
        _associate_policy_decision(db, action)
    elif action.action_type == "security.unban.asn_policy_reclassified":
        _finalize_reclassification_release(db, action)
    elif action.action_type in {"security.unban", "crowdsec_unban"}:
        _add_exception_after_manual_unban(db, action)


def _associate_policy_decision(db: Session, action: Action) -> None:
    parameters = action.parameters or {}
    policy = _validated_internal_policy(db, parameters, require_active=True)
    enforcement = _validated_enforcement(db, policy, normalize_global_ip(action.target), parameters)
    scenario = policy_scenario(policy.asn)
    candidates = (
        db.query(CrowdSecDecision)
        .filter(
            CrowdSecDecision.ip == enforcement.ip,
            CrowdSecDecision.decision_type == "ban",
            CrowdSecDecision.origin == "opensecdash",
            CrowdSecDecision.scenario == scenario,
        )
        .all()
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"Could not uniquely associate ASN policy decision for {enforcement.ip}: {len(candidates)} candidates"
        )
    decision = candidates[0]
    enforcement.action_id = action.id
    enforcement.decision_id = decision.decision_id
    enforcement.decision_until = decision.until
    enforcement.scenario = decision.scenario
    enforcement.release_pending = False
    enforcement.release_error = None


def _finalize_reclassification_release(db: Session, action: Action) -> None:
    parameters = action.parameters or {}
    policy = _validated_internal_policy(db, parameters, require_active=False)
    enforcement = _validated_enforcement(db, policy, normalize_global_ip(action.target), parameters)
    enforcement.decision_id = None
    enforcement.decision_until = None
    enforcement.scenario = None
    enforcement.last_observed_asn = normalize_asn(parameters.get("new_asn"))
    enforcement.release_pending = False
    enforcement.release_error = None


def enrich_manual_unban_parameters(
    db: Session,
    ip: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    from .decisions import active_decision_by_id, active_decision_for_ip

    decision_id = str(parameters.get("decision_id") or "").strip()
    decision = active_decision_by_id(db, decision_id) if decision_id else active_decision_for_ip(db, ip)
    if decision is None:
        raise ValueError("No active CrowdSec ban decision found for this IP")
    if normalize_global_ip(decision.ip) != normalize_global_ip(ip):
        raise ValueError("CrowdSec decision id does not match the active ban for this IP")
    enforcement = (
        db.query(CrowdSecAsnBanEnforcement)
        .join(CrowdSecAsnBan)
        .filter(
            CrowdSecAsnBanEnforcement.decision_id == decision.decision_id,
            CrowdSecAsnBan.status == "active",
        )
        .first()
    )
    enriched = {**parameters, "decision_id": decision.decision_id}
    if enforcement is not None:
        enriched.update(
            {
                "asn": enforcement.asn_ban.asn,
                "asn_ban_id": enforcement.asn_ban_id,
                "enforcement_id": enforcement.id,
            }
        )
    return enriched


def _add_exception_after_manual_unban(db: Session, action: Action) -> None:
    parameters = action.parameters or {}
    if not parameters.get("asn_ban_id") or not parameters.get("enforcement_id"):
        return
    policy = _validated_internal_policy(db, parameters, require_active=True)
    ip = normalize_global_ip(action.target)
    enforcement = _validated_enforcement(db, policy, ip, parameters)
    existing = (
        db.query(CrowdSecAsnBanException)
        .filter(
            CrowdSecAsnBanException.asn_ban_id == policy.id,
            CrowdSecAsnBanException.ip == ip,
        )
        .first()
    )
    if existing is None:
        db.add(CrowdSecAsnBanException(asn_ban_id=policy.id, ip=ip, source_action_id=action.id))
        store_event(
            db,
            source="Action Framework",
            source_id="actions",
            plugin="crowdsec",
            plugin_id="crowdsec",
            event_type="security.asn_ban.exception.added",
            severity="info",
            ip=ip,
            data_json={"asn": policy.asn, "asn_ban_id": policy.id, "action_id": action.id},
        )
    enforcement.decision_id = None
    enforcement.decision_until = None
    enforcement.scenario = None
    enforcement.release_pending = False
    enforcement.release_error = None


def reconcile_enforcements_after_sync(db: Session) -> None:
    decisions = {
        decision.decision_id: decision
        for decision in db.query(CrowdSecDecision).filter(CrowdSecDecision.decision_type == "ban").all()
    }
    for enforcement in (
        db.query(CrowdSecAsnBanEnforcement)
        .filter(CrowdSecAsnBanEnforcement.decision_id.isnot(None))
        .all()
    ):
        decision = decisions.get(str(enforcement.decision_id))
        expected_scenario = policy_scenario(enforcement.asn_ban.asn)
        if decision is None:
            enforcement.decision_id = None
            enforcement.decision_until = None
            enforcement.scenario = None
            enforcement.release_pending = False
            enforcement.release_error = None
            continue
        owned = (
            decision.ip == enforcement.ip
            and decision.origin == "opensecdash"
            and decision.scenario == expected_scenario
        )
        if not owned:
            enforcement.decision_id = None
            enforcement.decision_until = None
            enforcement.scenario = None
            enforcement.release_pending = False
            enforcement.release_error = "Stored decision no longer matches ASN policy ownership"
            continue
        enforcement.decision_until = decision.until
        enforcement.scenario = decision.scenario


def retry_pending_policy_work(db: Session) -> None:
    if (
        _actions_simulated(db)
        or not _plugin_enabled(db, "crowdsec")
        or not _lapi_configuration_ready(db)
    ):
        return
    pending = (
        db.query(CrowdSecAsnBanEnforcement)
        .filter(
            CrowdSecAsnBanEnforcement.release_pending == True,  # noqa: E712
            CrowdSecAsnBanEnforcement.decision_id.isnot(None),
        )
        .all()
    )
    for enforcement in pending:
        try:
            observed_asn = normalize_asn(enforcement.last_observed_asn)
        except ValueError:
            enforcement.release_error = "Pending release has no valid reclassified ASN"
            continue
        _run_reclassification_release(
            db,
            enforcement,
            observed_asn,
        )
    for policy in db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.status == "removing").all():
        if _finish_policy_removal(db, policy):
            store_event(
                db,
                source="CrowdSec policy retry",
                source_id="crowdsec-policy-retry",
                plugin="crowdsec",
                plugin_id="crowdsec",
                event_type="security.asn_ban.disabled",
                severity="info",
                data_json={"asn": policy.asn, "asn_ban_id": policy.id, "manual": False, "trigger": "retry"},
            )


def _finish_policy_removal(db: Session, policy: CrowdSecAsnBan) -> bool:
    from .decisions import active_decision_by_id, sync_crowdsec_decisions
    from .lapi import LapiError, lapi_delete_decision, lapi_login

    ok, message = sync_crowdsec_decisions(db, force=True)
    if not ok:
        policy.removal_error = message
        return False
    errors: list[str] = []
    url = get_setting_value(db, "plugin.crowdsec.lapi_url", "")
    try:
        token = lapi_login(
            url,
            get_setting_value(db, "plugin.crowdsec.lapi_login", ""),
            get_setting_value(db, "plugin.crowdsec.lapi_password", ""),
        )
    except LapiError as exc:
        policy.removal_error = str(exc)
        return False
    for enforcement in policy.enforcements:
        decision_id = str(enforcement.decision_id or "").strip()
        if not decision_id:
            continue
        decision = active_decision_by_id(db, decision_id)
        if decision is None:
            enforcement.decision_id = None
            enforcement.decision_until = None
            enforcement.scenario = None
            continue
        if not (
            decision.ip == enforcement.ip
            and decision.origin == "opensecdash"
            and decision.scenario == policy_scenario(policy.asn)
        ):
            errors.append(f"Decision {decision_id} no longer matches policy ownership")
            continue
        try:
            lapi_delete_decision(url, token, decision_id)
        except LapiError as exc:
            errors.append(f"Decision {decision_id}: {exc}")
            continue
        enforcement.decision_id = None
        enforcement.decision_until = None
        enforcement.scenario = None
    if errors:
        policy.removal_error = "; ".join(errors)
        return False
    policy.removal_error = None
    db.delete(policy)
    return True
