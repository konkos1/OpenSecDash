import asyncio
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.database.dependencies import get_db
from app.models.events import Event
from app.models.saved_views import SavedView
from app.plugins.manager import get_plugin_manager
from app.services.events import apply_event_filters, tokenize_search_expression
from app.services.saved_views import plugin_views_for_scope, view_filters_from_query, view_to_query
from app.web.guards import is_plugin_enabled
from app.web.render import render
from app.web.tables import (
    DEFAULT_ACCESS_COLUMNS,
    asset_links_for_events,
    clean_filter_value,
    clean_time_range,
    column_redirect_url,
    parse_snapshot_before,
    save_setting,
    save_table_columns,
    table_columns,
    time_range_start,
    today_start,
    utc_search_terms_for_ui_time,
)

# Enabled-gated by the plugin router mount (see app.main); no require_plugin_enabled here.
router = APIRouter(tags=["traefik_log"])


def _saved_view_context(db: Session, request: Request) -> dict[str, object]:
    query_params = request.query_params
    query_items = query_params.multi_items() if hasattr(query_params, "multi_items") else query_params.items()
    return_query = urlencode([(key, value) for key, value in query_items if key != "view_error"])
    plugin_views = plugin_views_for_scope(
        [
            view
            for view in get_plugin_manager().default_views()
            if is_plugin_enabled(db, str(view.get("plugin_id", "")))
        ],
        "access",
    )
    for view in plugin_views:
        query = view_to_query(view["filter_json"])
        view["href"] = f"/access?{query}" if query else "/access"
    saved_views = db.query(SavedView).filter(SavedView.scope == "access").order_by(SavedView.created_at.desc()).all()
    return {
        "plugin_views": plugin_views,
        "saved_views": saved_views,
        "current_view_query": view_to_query(view_filters_from_query(query_items)),
        "current_view_return_query": return_query,
        "view_error": str(query_params.get("view_error", "")),
    }


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
    range: str | None = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    db: Session = Depends(get_db),
):
    q_value = clean_filter_value(q)
    country_in_values = [value for item in (country_in or "").split(",") if (value := clean_filter_value(item))]
    timezone_name = get_setting_value(db, "timezone", "auto")
    q_tokens = [token for token in tokenize_search_expression(q_value or "") if token not in {"&&", "||", "(", ")"}]
    q_utc_terms_by_term = {token: utc_search_terms_for_ui_time(token, timezone_name) for token in q_tokens}
    today_enabled = today == "true"
    snapshot_cutoff = parse_snapshot_before(snapshot_before)
    range_value = clean_time_range(range)
    if range is None:
        range_value = clean_time_range(get_setting_value(db, "ui.time_range", ""))
    elif range_value:
        save_setting(db, "ui.time_range", range_value)
        db.commit()
    range_start = time_range_start(range_value, from_)
    custom_to = parse_snapshot_before(to) if range_value == "custom" else None
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
        "event_time_from": max([value for value in [today_start(db) if today_enabled else None, range_start] if value is not None], default=None),
        "event_time_to": min([value for value in [snapshot_cutoff, custom_to] if value is not None], default=None),
    }
    # Access renders its bounded table immediately. Its shared live-mode code
    # already replaces only #access-results for later event notifications.
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    event_asset_links = asset_links_for_events(db, events)
    column_options, active_columns = table_columns(db, "ui.access.visible_columns", DEFAULT_ACCESS_COLUMNS)
    saved_view_context = _saved_view_context(db, request)
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
        range=range_value or "",
        snapshot_before=snapshot_before or "",
        view_to_query=view_to_query,
        **saved_view_context,
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
