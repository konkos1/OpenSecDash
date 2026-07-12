import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.database.dependencies import get_db
from app.models.events import Event
from app.services.events import apply_event_filters, tokenize_search_expression
from app.web.render import render
from app.web.tables import (
    DEFAULT_ACCESS_COLUMNS,
    asset_links_for_events,
    clean_filter_value,
    column_redirect_url,
    parse_snapshot_before,
    save_table_columns,
    table_columns,
    today_start,
    utc_search_terms_for_ui_time,
)

# Enabled-gated by the plugin router mount (see app.main); no require_plugin_enabled here.
router = APIRouter(tags=["traefik_log"])


@router.get("/access")
def access_page(
    request: Request,
    q: str | None = None,
    asn: str | None = None,
    hostname: str | None = None,
    asset: str | None = None,
    status_min: str | None = None,
    status_max: str | None = None,
    country_not: str | None = None,
    country_in: str | None = None,
    hide_local_ips: str | None = None,
    show_local_ips: str | None = None,
    today: str | None = None,
    snapshot_before: str | None = None,
    db: Session = Depends(get_db),
):
    q_value = clean_filter_value(q)
    country_in_values = [value for item in (country_in or "").split(",") if (value := clean_filter_value(item))]
    timezone_name = get_setting_value(db, "timezone", "auto")
    q_tokens = [token for token in tokenize_search_expression(q_value or "") if token not in {"&&", "||", "(", ")"}]
    q_utc_terms_by_term = {token: utc_search_terms_for_ui_time(token, timezone_name) for token in q_tokens}
    today_enabled = today == "true"
    snapshot_cutoff = parse_snapshot_before(snapshot_before)
    local_filter_touched = "local_ip_filter" in request.query_params
    hide_local_default = get_setting_value(db, "plugin.traefik_log.hide_local_ips_default", "false") == "true"
    hide_local_enabled = hide_local_ips == "true" or (hide_local_ips is None and show_local_ips is None and not local_filter_touched and hide_local_default)
    show_local_enabled = show_local_ips == "true"
    filters = {
        "event_type": "access.*",
        "q": q_value,
        "q_utc_terms": utc_search_terms_for_ui_time(q_value, timezone_name),
        "q_utc_terms_by_term": q_utc_terms_by_term,
        "asn": clean_filter_value(asn),
        "hostname": clean_filter_value(hostname),
        "asset": clean_filter_value(asset),
        "status_code_min": clean_filter_value(status_min),
        "status_code_max": clean_filter_value(status_max),
        "country_not": clean_filter_value(country_not),
        "country_in": country_in_values,
        "plugins": ["traefik_log"],
        "hide_local_ips": hide_local_enabled,
        "show_local_ips": show_local_enabled,
        "event_time_from": today_start(db) if today_enabled else None,
        "event_time_to": snapshot_cutoff,
    }
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    column_options, active_columns = table_columns(db, "ui.access.visible_columns", DEFAULT_ACCESS_COLUMNS)
    event_asset_links = asset_links_for_events(db, events)
    return render(
        request,
        db,
        "traefik_log/access.html",
        events=events,
        event_asset_links=event_asset_links,
        column_options=column_options,
        active_columns=active_columns,
        columns_setting_action="/access/columns",
        q=q or "",
        hide_local_ips=hide_local_enabled,
        show_local_ips=show_local_enabled,
        today=today_enabled,
        snapshot_before=snapshot_before or "",
        live_default=get_setting_value(db, "live_default", "true"),
    )


@router.post("/access/columns")
async def save_access_columns(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    # In a thread for the same reason as save_events_columns.
    def _save() -> None:
        save_table_columns(db, "ui.access.visible_columns", [str(value) for value in form.getlist("columns")], DEFAULT_ACCESS_COLUMNS)
        db.commit()

    await asyncio.to_thread(_save)
    return RedirectResponse(url=column_redirect_url(request, "/access", str(form.get("snapshot_before") or "")), status_code=303)
