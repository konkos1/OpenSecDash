from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.core.time import utc_now
from app.database.dependencies import get_db
from app.models.core import Action, CrowdSecDecision
from app.models.events import Event
from app.plugins.manager import get_plugin_manager
from app.services.actions import ActionAlreadyRunning, create_action
from app.services.events import store_event
from app.web.render import render

from .services.decisions import crowdsec_cscli_status, sync_crowdsec_decisions

# Enabled-gated by the plugin router mount (see app.main); no require_plugin_enabled here.
router = APIRouter(tags=["crowdsec"])
# Mounted without the enabled-guard: the ban/unban form target must also work
# in dry-run while the plugin is disabled (the IP-explorer panel shows it then).
ungated_router = APIRouter(tags=["crowdsec"])


@router.get("/crowdsec")
def crowdsec_page(request: Request, db: Session = Depends(get_db)):
    bans = db.query(Event).filter(Event.event_type.startswith("security.ban")).order_by(Event.event_time.desc()).limit(100).all()
    active_decisions = {decision.ip: decision for decision in db.query(CrowdSecDecision).filter(CrowdSecDecision.decision_type == "ban").all()}
    # Count scenarios in SQL instead of deserializing every ban event's JSON
    # payload in Python on each page view - this table grows without bound.
    scenario_expr = func.coalesce(func.json_extract(Event.data_json, "$.scenario"), "")
    scenarios = [
        (str(scenario) or None, int(count))
        for scenario, count in (
            db.query(scenario_expr, func.count(Event.id))
            .filter(Event.event_type.startswith("security.ban"))
            .group_by(scenario_expr)
            .order_by(func.count(Event.id).desc())
            .limit(10)
            .all()
        )
    ]
    countries = (
        db.query(Event.country, func.count(Event.id))
        .filter(Event.event_type.startswith("security.ban"), Event.country.isnot(None))
        .group_by(Event.country)
        .order_by(func.count(Event.id).desc())
        .limit(10)
        .all()
    )
    return render(
        request,
        db,
        "crowdsec/crowdsec.html",
        bans=bans,
        scenarios=scenarios,
        countries=countries,
        active_decisions=active_decisions,
        cscli_status=crowdsec_cscli_status(db),
        action_dry_run=get_setting_value(db, "action_dry_run", "true").lower() == "true",
    )


@router.post("/crowdsec/decisions/refresh")
def crowdsec_decisions_refresh(request: Request, db: Session = Depends(get_db)):
    sync_crowdsec_decisions(db, force=True)
    db.commit()
    next_url = str(request.query_params.get("next") or "/crowdsec")
    if not next_url.startswith("/"):
        next_url = "/crowdsec"
    return RedirectResponse(url=next_url, status_code=303)


@ungated_router.post("/actions/ip")
def action_ip_page(
    action_type: str = Form(...),
    ip: str = Form(...),
    duration: str = Form("4h"),
    decision_id: str = Form(""),
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    # Told to CrowdSec itself (as the LAPI/cscli decision reason) and stored
    # on the resulting event, so both CrowdSec's own tooling and the CrowdSec
    # page clearly show this was a manual OpenSecDash action instead of a
    # generic, unidentifiable "Manual action".
    is_ban = action_type in {"security.ban", "crowdsec_ban"}
    reason = "Manual ban via OpenSecDash" if is_ban else "Manual unban via OpenSecDash"
    try:
        create_action(db, action_type, ip, "ip", {"duration": duration, "reason": reason, "decision_id": decision_id}, confirmed)
    except ActionAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        manager = get_plugin_manager()
        action = Action(
            timestamp=utc_now().replace(tzinfo=None),
            action_type=action_type,
            plugin_id=manager.plugin_id_for_action(action_type),
            target_type="ip",
            target=ip,
            parameters={"duration": duration, "reason": reason, "decision_id": decision_id},
            status="failed",
            result=str(exc),
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
            ip=ip,
            data_json={"action_id": action.id, "action_type": action_type, "target": ip, "status": "failed", "result": str(exc), "manual": True, "trigger": "manual"},
        )
        db.commit()
    return RedirectResponse(url=f"/ip/{quote(ip, safe='')}", status_code=303)
