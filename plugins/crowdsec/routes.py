from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.database.dependencies import get_db
from app.models.core import CrowdSecDecision
from app.models.events import Event
from app.web.render import render

from .services.decisions import crowdsec_lapi_status, sync_crowdsec_decisions
from .services.rollups import _top_rollup_metric

# Enabled-gated by the plugin router mount (see app.main); no require_plugin_enabled here.
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
    scenarios: list = []
    countries: list = []
    if is_data_request:
        bans = db.query(Event).filter(Event.event_type.startswith("security.ban")).order_by(Event.event_time.desc()).limit(100).all()
        active_decisions = {decision.ip: decision for decision in db.query(CrowdSecDecision).filter(CrowdSecDecision.decision_type == "ban").all()}
        scenarios = _top_rollup_metric(db, "scenario", 10)
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
        crowdsec_deferred=not is_data_request,
        bans=bans,
        scenarios=scenarios,
        countries=countries,
        active_decisions=active_decisions,
        lapi_status=crowdsec_lapi_status(db),
        action_dry_run=get_setting_value(db, "action_dry_run", "true").lower() == "true",
    )


@router.post("/crowdsec/decisions/refresh")
def crowdsec_decisions_refresh(request: Request, db: Session = Depends(get_db)):
    sync_crowdsec_decisions(db, force=True)
    db.commit()
    next_url = str(request.query_params.get("next") or "/crowdsec")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/crowdsec"
    return RedirectResponse(url=next_url, status_code=303)
