from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.database.dependencies import get_db
from app.models.core import AggregationDaily, AggregationMonthly, CrowdSecDecision
from app.models.events import Event
from app.web.render import render

from .services.decisions import crowdsec_cscli_status, sync_crowdsec_decisions

# Enabled-gated by the plugin router mount (see app.main); no require_plugin_enabled here.
router = APIRouter(tags=["crowdsec"])


def _top_rollup_metric(db: Session, metric: str, limit: int) -> list[tuple[str | None, int]]:
    """Return all-time top values from compacted monthly plus open daily rollups.

    This keeps the CrowdSec overview off the raw events table for expensive
    scenario/country GROUP BY queries. Monthly rollups contain completed
    compacted days; daily rollups contain the current/open periods.
    """
    totals: dict[str, int] = {}
    for key, value in db.query(AggregationMonthly.key, func.sum(AggregationMonthly.value)).filter(AggregationMonthly.metric == metric).group_by(AggregationMonthly.key).all():
        totals[str(key)] = totals.get(str(key), 0) + int(value or 0)
    for key, value in db.query(AggregationDaily.key, func.sum(AggregationDaily.value)).filter(AggregationDaily.metric == metric).group_by(AggregationDaily.key).all():
        totals[str(key)] = totals.get(str(key), 0) + int(value or 0)
    return [
        (key or None, value)
        for key, value in sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


@router.get("/crowdsec")
def crowdsec_page(request: Request, db: Session = Depends(get_db)):
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
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/crowdsec"
    return RedirectResponse(url=next_url, status_code=303)
