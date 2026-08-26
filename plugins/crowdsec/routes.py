from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.database.dependencies import get_db
from app.models.core import Action, CrowdSecAsnBan, CrowdSecAsnBanException, CrowdSecDecision
from app.models.events import Event
from app.plugins.manager import get_plugin_manager
from app.services.actions import create_action
from app.services.events import store_event
from app.web.redirects import safe_local_redirect_target
from app.web.render import render

from .services.decisions import crowdsec_lapi_status, sync_crowdsec_decisions
from .services.policies import (
    POLICY_SCENARIO_GROUP,
    policy_management_rows,
    policy_ui_state,
)
from .services.rollups import _top_rollup_metric

# Ungated so stored ASN policies and exceptions stay manageable while CrowdSec is disabled.
router = APIRouter(tags=["crowdsec"])


@router.get("/crowdsec")
def crowdsec_page(request: Request, db: Session = Depends(get_db)):
    # Progressive loading (docs/internal/progressive-widget-loading/): the LAPI
    # status card stays in the shell (one cheap indexed Diagnostic read); the
    # ban/scenario/country/decision panels load via the HX-Request that the load
    # trigger and the auto-refresh send.
    is_data_request = request.headers.get("HX-Request") == "true"
    bans: list[Event] = []
    active_decisions: dict[str | None, CrowdSecDecision] = {}
    policy_asn_by_decision: dict[str, str] = {}
    asn_policies: list[dict] = []
    asn_policy_state: dict = {}
    scenarios: list = []
    countries: list = []
    if is_data_request:
        bans = db.query(Event).filter(Event.event_type.startswith("security.ban")).order_by(Event.event_time.desc()).limit(100).all()
        active_decisions = {decision.ip: decision for decision in db.query(CrowdSecDecision).filter(CrowdSecDecision.decision_type == "ban").all()}
        scenarios = [
            {
                "key": scenario or "unknown",
                "label_key": (
                    "crowdsec.scenario.manual_permanent_asn_ban"
                    if scenario == POLICY_SCENARIO_GROUP
                    else None
                ),
                "count": count,
            }
            for scenario, count in _top_rollup_metric(db, "scenario", 10)
        ]
        countries = (
            db.query(Event.country, func.count(Event.id))
            .filter(Event.event_type.startswith("security.ban"), Event.country.isnot(None))
            .group_by(Event.country)
            .order_by(func.count(Event.id).desc())
            .limit(10)
            .all()
        )
        asn_policies = policy_management_rows(db)
        asn_policy_state = policy_ui_state(db)
        policy_asn_by_decision = {
            str(decision["decision_id"]): str(row["policy"].asn)
            for row in asn_policies
            for decision in row["active_decisions"]
        }
    return render(
        request,
        db,
        "crowdsec/crowdsec.html",
        crowdsec_deferred=not is_data_request,
        bans=bans,
        scenarios=scenarios,
        countries=countries,
        active_decisions=active_decisions,
        policy_asn_by_decision=policy_asn_by_decision,
        asn_policies=asn_policies,
        asn_policy_state=asn_policy_state,
        lapi_status=crowdsec_lapi_status(db),
        action_dry_run=get_setting_value(db, "action_dry_run", "true").lower() == "true",
    )


@router.post("/crowdsec/decisions/refresh")
def crowdsec_decisions_refresh(request: Request, db: Session = Depends(get_db)):
    sync_crowdsec_decisions(db, force=True)
    db.commit()
    next_url = safe_local_redirect_target(request, request.query_params.get("next"), "/crowdsec")
    return RedirectResponse(url=next_url, status_code=303)


def _record_failed_form_action(
    db: Session,
    action_type: str,
    target: str,
    target_type: str,
    parameters: dict[str, object],
    error: ValueError,
) -> None:
    manager = get_plugin_manager()
    action = Action(
        timestamp=utc_now().replace(tzinfo=None),
        action_type=action_type,
        plugin_id=manager.plugin_id_for_action(action_type),
        target_type=target_type,
        target=target,
        parameters=parameters,
        status="failed",
        result=str(error),
        requires_confirmation=action_type in manager.critical_action_types(),
    )
    db.add(action)
    db.flush()
    store_event(
        db,
        source="Action Framework",
        source_id="actions",
        plugin=action.plugin_id,
        plugin_id=action.plugin_id,
        event_type="action.failed",
        severity="error",
        ip=target if target_type == "ip" else None,
        data_json={
            "action_id": action.id,
            "action_type": action_type,
            "target": target,
            "status": "failed",
            "result": str(error),
            "manual": True,
            "trigger": "manual",
        },
    )
    db.commit()


def _run_form_action(
    db: Session,
    action_type: str,
    target: str,
    target_type: str,
    parameters: dict[str, object],
    confirmed: bool,
) -> None:
    try:
        create_action(db, action_type, target, target_type, parameters, confirmed)
    except ValueError as exc:
        db.rollback()
        _record_failed_form_action(db, action_type, target, target_type, parameters, exc)


def _policy_or_404(db: Session, policy_id: int) -> CrowdSecAsnBan:
    policy = db.query(CrowdSecAsnBan).filter(CrowdSecAsnBan.id == policy_id).first()
    if policy is None:
        raise HTTPException(status_code=404, detail="Permanent ASN ban not found")
    return policy


@router.post("/crowdsec/asn-bans/enable")
def crowdsec_asn_ban_enable(
    request: Request,
    event_id: int = Form(...),
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None or not event.asn:
        raise HTTPException(status_code=404, detail="Source event not found")
    _run_form_action(
        db,
        "security.asn_ban.enable",
        event.asn,
        "asn",
        {"event_id": event.id},
        confirmed,
    )
    return RedirectResponse(
        url=safe_local_redirect_target(request, request.query_params.get("next"), "/crowdsec"),
        status_code=303,
    )


@router.post("/crowdsec/asn-bans/{policy_id}/disable")
def crowdsec_asn_ban_disable(
    policy_id: int,
    request: Request,
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    policy = _policy_or_404(db, policy_id)
    _run_form_action(db, "security.asn_ban.disable", policy.asn, "asn", {}, confirmed)
    return RedirectResponse(
        url=safe_local_redirect_target(request, request.query_params.get("next"), "/crowdsec"),
        status_code=303,
    )


@router.post("/crowdsec/asn-bans/{policy_id}/exceptions/{exception_id}/remove")
def crowdsec_asn_ban_exception_remove(
    policy_id: int,
    exception_id: int,
    request: Request,
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    exception = (
        db.query(CrowdSecAsnBanException)
        .filter(
            CrowdSecAsnBanException.id == exception_id,
            CrowdSecAsnBanException.asn_ban_id == policy_id,
        )
        .first()
    )
    if exception is None:
        raise HTTPException(status_code=404, detail="ASN policy exception not found")
    _run_form_action(
        db,
        "security.asn_ban.exception.remove",
        exception.ip,
        "ip",
        {"asn_ban_id": policy_id},
        confirmed,
    )
    return RedirectResponse(
        url=safe_local_redirect_target(request, request.query_params.get("next"), "/crowdsec"),
        status_code=303,
    )


@router.post("/crowdsec/asn-bans/{policy_id}/provider-change/acknowledge")
def crowdsec_asn_ban_provider_change_acknowledge(
    policy_id: int,
    request: Request,
    provider_name_changed_at: str = Form(...),
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    policy = _policy_or_404(db, policy_id)
    _run_form_action(
        db,
        "security.asn_ban.provider_change.acknowledge",
        policy.asn,
        "asn",
        {"provider_name_changed_at": provider_name_changed_at},
        confirmed,
    )
    return RedirectResponse(
        url=safe_local_redirect_target(request, request.query_params.get("next"), "/crowdsec"),
        status_code=303,
    )
