from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup, escape
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.template_context import build_template_context, get_setting_value
from app.core.time import datetime_iso_utc, format_datetime_for_timezone, local_day_start_as_utc, utc_now
from app.database.dependencies import get_db
from app.models.assets import Asset
from app.models.core import Action, Datasource, Diagnostic, Insight, PluginRecord
from app.models.events import Event
from app.models.settings import Setting
from app.models.systems import System
from app.services.apps_inventory_import import import_apps_inventory
from app.services.apps_inventory_source import load_asset_source
from app.services.apps_inventory_updates import refresh_asset_updates
from app.plugins.manager import get_plugin_manager
from app.services.actions import create_action
from app.services.events import apply_event_filters

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


@pass_context
def format_duration(context, value: str | None) -> str:
    if not value:
        return "-"
    match = __import__("re").match(r"^(\d+)([smhdw])$", str(value).strip().lower())
    if not match:
        return str(value)
    amount = int(match.group(1))
    unit = match.group(2)
    language = str(context.get("language", "en"))
    labels = {
        "de": {"s": ("Sekunde", "Sekunden"), "m": ("Minute", "Minuten"), "h": ("Stunde", "Stunden"), "d": ("Tag", "Tage"), "w": ("Woche", "Wochen")},
        "en": {"s": ("second", "seconds"), "m": ("minute", "minutes"), "h": ("hour", "hours"), "d": ("day", "days"), "w": ("week", "weeks")},
    }
    singular, plural = labels.get(language, labels["en"])[unit]
    return f"{amount} {singular if amount == 1 else plural}"


@pass_context
def format_country_name(context, value: str | None) -> Markup | str:
    if not value:
        return "-"
    code = str(value).upper()
    return Markup('<span class="osd-country" data-country-code="{}">{}</span>'.format(escape(code), escape(code)))


@pass_context
def format_datetime(context, value: datetime | None) -> Markup | str:
    if value is None:
        return "-"
    timezone = str(context.get("timezone", "auto"))
    text = format_datetime_for_timezone(value, timezone)
    iso_utc = datetime_iso_utc(value)
    return Markup(
        '<span class="osd-datetime" data-datetime-utc="{}" data-timezone="{}">{}</span>'.format(
            escape(iso_utc),
            escape(timezone),
            escape(text),
        )
    )


def url_path_quote(value: str | None) -> str:
    return quote(str(value or ""), safe="")


templates.env.filters["datetime"] = format_datetime
templates.env.filters["duration"] = format_duration
templates.env.filters["country_name"] = format_country_name
templates.env.filters["url_path_quote"] = url_path_quote


def save_setting(db: Session, key: str, value: str) -> None:
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting is None:
        db.add(Setting(key=key, value=value))
    else:
        setting.value = value


def today_start(db: Session) -> datetime:
    return local_day_start_as_utc(get_setting_value(db, "timezone", "auto"))


def render(request: Request, db: Session, template: str, **context):
    return templates.TemplateResponse(request=request, name=template, context={**build_template_context(db), **context})


def is_plugin_enabled(db: Session, plugin_id: str) -> bool:
    return get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"


def events_feature_enabled(db: Session) -> bool:
    return any(is_plugin_enabled(db, plugin_id) for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"])


def require_plugin_enabled(db: Session, plugin_id: str) -> None:
    if not is_plugin_enabled(db, plugin_id):
        raise HTTPException(status_code=404, detail="Feature is disabled")


def require_events_feature_enabled(db: Session) -> None:
    if not events_feature_enabled(db):
        raise HTTPException(status_code=404, detail="Feature is disabled")


@router.get("/")
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    since = today_start(db)
    enabled_plugins = {
        "apps_inventory": is_plugin_enabled(db, "apps_inventory"),
        "crowdsec": is_plugin_enabled(db, "crowdsec"),
        "geoblock_log": is_plugin_enabled(db, "geoblock_log"),
        "traefik_log": is_plugin_enabled(db, "traefik_log"),
    }
    country_data_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"]
        if enabled_plugins[plugin_id]
    ]

    event_count = db.query(Event).filter(Event.event_time >= since, Event.plugin.in_(country_data_plugins)).count() if country_data_plugins else 0
    active_bans = db.query(Event).filter(Event.event_type == "security.ban", Event.event_time >= since, Event.plugin == "crowdsec").count() if enabled_plugins["crowdsec"] else 0
    geoblocks = db.query(Event).filter(Event.event_type == "security.geoblock", Event.event_time >= since, Event.plugin == "geoblock_log").count() if enabled_plugins["geoblock_log"] else 0
    access_events = db.query(Event).filter(Event.event_type.startswith("access."), Event.event_time >= since, Event.plugin == "traefik_log").count() if enabled_plugins["traefik_log"] else 0
    top_countries = []
    if country_data_plugins:
        top_countries = (
            db.query(Event.country, func.count(Event.id))
            .filter(
                Event.country.isnot(None),
                Event.event_time >= since,
                Event.plugin.in_(country_data_plugins),
            )
            .group_by(Event.country)
            .order_by(func.count(Event.id).desc())
            .limit(5)
            .all()
        )
    latest_security_events = []
    security_data_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log"]
        if enabled_plugins[plugin_id]
    ]
    if security_data_plugins:
        latest_security_events = (
            db.query(Event)
            .filter(Event.event_type.startswith("security."), Event.plugin.in_(security_data_plugins))
            .order_by(Event.event_time.desc())
            .limit(8)
            .all()
        )
    widgets = []
    if enabled_plugins["crowdsec"]:
        widgets.append({"title_key": "dashboard.active_bans", "value": active_bans, "href": "/events?event_type=security.ban"})
    if enabled_plugins["geoblock_log"]:
        widgets.append({"title_key": "dashboard.geoblocks_today", "value": geoblocks, "href": "/events?event_type=security.geoblock"})
    if enabled_plugins["traefik_log"]:
        widgets.append({"title_key": "dashboard.access_today", "value": access_events, "href": "/events?event_type=access.*"})
    if enabled_plugins["apps_inventory"]:
        widgets.extend(
            [
                {"title_key": "dashboard.assets", "value": db.query(Asset).filter(Asset.is_active == True).count(), "href": "/assets"},
                {"title_key": "dashboard.updates", "value": db.query(Asset).filter(Asset.update_available == True).count(), "href": "/assets?updates=true"},
            ]
        )

    return render(
        request,
        db,
        "dashboard.html",
        widgets=widgets,
        enabled_plugins=enabled_plugins,
        top_countries=top_countries,
        country_data_plugins=country_data_plugins,
        latest_security_events=latest_security_events,
    )


def clean_filter_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def utc_search_terms_for_ui_time(q: str | None, timezone_name: str) -> list[str]:
    if not q:
        return []
    text = q.strip()
    try:
        timezone = ZoneInfo(timezone_name) if timezone_name and timezone_name != "auto" else None
    except ZoneInfoNotFoundError:
        timezone = None
    if timezone is None:
        return []

    formats = (
        ("%Y-%m-%d %H:%M:%S", "datetime_seconds"),
        ("%Y-%m-%d %H:%M", "datetime_minutes"),
        ("%Y-%m-%d", "date"),
        ("%H:%M:%S", "time_seconds"),
        ("%H:%M", "time_minutes"),
    )
    terms: list[str] = []
    for fmt, kind in formats:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if kind.startswith("time"):
            today = utc_now().astimezone(timezone)
            parsed = parsed.replace(year=today.year, month=today.month, day=today.day)
        utc_value = parsed.replace(tzinfo=timezone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        if kind == "datetime_seconds":
            terms.append(utc_value.strftime("%Y-%m-%d %H:%M:%S"))
        elif kind == "datetime_minutes":
            terms.append(utc_value.strftime("%Y-%m-%d %H:%M"))
        elif kind == "date":
            terms.append(utc_value.strftime("%Y-%m-%d"))
        elif kind == "time_seconds":
            terms.append(utc_value.strftime("%H:%M:%S"))
        elif kind == "time_minutes":
            terms.append(utc_value.strftime("%H:%M"))
    return list(dict.fromkeys(terms))


@router.get("/events")
def events_page(
    request: Request,
    event_type: str | None = None,
    ip: str | None = None,
    country: str | None = None,
    status_code: str | None = None,
    path: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    require_events_feature_enabled(db)
    status_code_value = clean_filter_value(status_code)
    country_value = clean_filter_value(country)
    if country_value and country_value != "-":
        country_value = country_value[:2].upper()
    q_value = clean_filter_value(q)
    timezone_name = get_setting_value(db, "timezone", "auto")
    enabled_event_plugins = [
        plugin_id
        for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"]
        if is_plugin_enabled(db, plugin_id)
    ]
    filters = {
        "event_type": clean_filter_value(event_type),
        "ip": clean_filter_value(ip),
        "country": country_value,
        "status_code": int(status_code_value) if status_code_value and status_code_value.isdigit() else None,
        "path": clean_filter_value(path),
        "q": q_value,
        "q_utc_terms": utc_search_terms_for_ui_time(q_value, timezone_name),
        "plugins": enabled_event_plugins,
    }
    form_values = {
        "event_type": event_type or "",
        "ip": ip or "",
        "country": country or "",
        "status_code": status_code or "",
        "path": path or "",
        "q": q or "",
    }
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    return render(request, db, "events.html", events=events, filters=form_values, live_default=get_setting_value(db, "live_default", "true"))


@router.get("/access")
def access_page(request: Request, q: str | None = None, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "traefik_log")
    filters = {"event_type": "access.*", "q": q}
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    return render(request, db, "access.html", events=events, q=q)


@router.get("/crowdsec")
def crowdsec_page(request: Request, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "crowdsec")
    bans = db.query(Event).filter(Event.event_type == "security.ban").order_by(Event.event_time.desc()).limit(100).all()
    scenario_counts: Counter[str] = Counter()
    scenario_rows = db.query(Event.data_json).filter(Event.event_type == "security.ban").all()
    for (data_json,) in scenario_rows:
        scenario = (data_json or {}).get("scenario") or ""
        scenario_counts[str(scenario or "")] += 1
    scenarios = [
        (scenario or None, count)
        for scenario, count in scenario_counts.most_common(10)
    ]
    countries = (
        db.query(Event.country, func.count(Event.id))
        .filter(Event.event_type == "security.ban", Event.country.isnot(None))
        .group_by(Event.country)
        .order_by(func.count(Event.id).desc())
        .limit(10)
        .all()
    )
    return render(request, db, "crowdsec.html", bans=bans, scenarios=scenarios, countries=countries)


@router.post("/actions/ip")
def action_ip_page(
    action_type: str = Form(...),
    ip: str = Form(...),
    duration: str = Form("4h"),
    confirmed: bool = Form(False),
    db: Session = Depends(get_db),
):
    try:
        create_action(db, action_type, ip, "ip", {"duration": duration, "reason": "Manual action"}, confirmed)
    except ValueError:
        pass
    return RedirectResponse(url=f"/ip/{quote(ip, safe='')}", status_code=303)


@router.get("/ip/{ip:path}")
def ip_explorer_page(ip: str, request: Request, db: Session = Depends(get_db)):
    require_events_feature_enabled(db)
    events = db.query(Event).filter(Event.ip == ip).order_by(Event.event_time.desc()).limit(200).all()
    raw_insights = db.query(Insight).filter(Insight.ip == ip).order_by(Insight.timestamp.desc()).limit(100).all()
    insights = []
    seen_insight_types = set()
    for insight in raw_insights:
        if insight.type in seen_insight_types:
            continue
        seen_insight_types.add(insight.type)
        insights.append(insight)
        if len(insights) >= 50:
            break
    enabled_plugins = {
        "crowdsec": is_plugin_enabled(db, "crowdsec"),
        "geoblock_log": is_plugin_enabled(db, "geoblock_log"),
        "traefik_log": is_plugin_enabled(db, "traefik_log"),
    }
    count_widgets = []
    if enabled_plugins["crowdsec"]:
        count_widgets.append(
            {
                "key": "bans",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type == "security.ban").count(),
                "href": f"/events?ip={ip}&event_type=security.ban",
            }
        )
    if enabled_plugins["geoblock_log"]:
        count_widgets.append(
            {
                "key": "geoblocks",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type == "security.geoblock").count(),
                "href": f"/events?ip={ip}&event_type=security.geoblock",
            }
        )
    if enabled_plugins["traefik_log"]:
        count_widgets.append(
            {
                "key": "access",
                "value": db.query(Event).filter(Event.ip == ip, Event.event_type.startswith("access.")).count(),
                "href": f"/events?ip={ip}&event_type=access.*",
            }
        )
    return render(
        request,
        db,
        "ip.html",
        ip=ip,
        events=events,
        insights=insights,
        count_widgets=count_widgets,
        crowdsec_enabled=enabled_plugins["crowdsec"],
    )


@router.get("/assets")
def assets_page(request: Request, show_inactive: bool = False, updates: bool = False, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "apps_inventory")
    systems = db.query(System).order_by(System.hostname).all()
    system_rows = []
    for system in systems:
        apps_query = db.query(Asset).filter(Asset.system_id == system.id)
        if not show_inactive:
            apps_query = apps_query.filter(Asset.is_active == True)
        if updates:
            apps_query = apps_query.filter(Asset.update_available == True)
        app_count = apps_query.count()
        if updates and app_count == 0:
            continue
        update_available = (
            apps_query.filter(Asset.update_available == True).count() > 0
        )
        latest_asset = apps_query.order_by(Asset.last_seen.desc()).first()
        last_seen = latest_asset.last_seen if latest_asset is not None else system.last_seen
        system_rows.append(
            {
                "system": system,
                "app_count": app_count,
                "update_available": update_available,
                "last_seen": last_seen,
            }
        )
    return render(request, db, "assets.html", system_rows=system_rows, show_inactive=show_inactive, updates=updates)


@router.get("/assets/system/{system_id}")
def asset_page(system_id: int, request: Request, show_inactive: bool = False, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "apps_inventory")
    system = db.query(System).filter(System.id == system_id).first()
    if system is None:
        raise HTTPException(status_code=404, detail="System not found")
    apps_query = db.query(Asset).filter(Asset.system_id == system.id)
    if not show_inactive:
        apps_query = apps_query.filter(Asset.is_active == True)
    apps = apps_query.order_by(Asset.name).all()
    app_ids = [asset.id for asset in apps]
    events = (
        db.query(Event)
        .filter(Event.asset_id.in_(app_ids))
        .order_by(Event.event_time.desc())
        .limit(100)
        .all()
        if app_ids
        else []
    )
    insights = (
        db.query(Insight)
        .filter(Insight.asset_id.in_(app_ids))
        .order_by(Insight.timestamp.desc())
        .limit(50)
        .all()
        if app_ids
        else []
    )
    mqtt_plugin_enabled = get_setting_value(db, "plugin.mqtt.enabled", "false") == "true"
    return render(
        request,
        db,
        "asset.html",
        system=system,
        apps=apps,
        events=events,
        insights=insights,
        show_inactive=show_inactive,
        mqtt_plugin_enabled=mqtt_plugin_enabled,
    )


@router.post("/assets/{asset_id}/mqtt")
def toggle_asset_mqtt(asset_id: int, enabled: str = Form("false"), db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not asset.version or not asset.latest_version:
        asset.mqtt_publish_enabled = False
        db.commit()
        return RedirectResponse(url=f"/assets/system/{asset.system_id}", status_code=303)
    asset.mqtt_publish_enabled = enabled == "true"
    db.commit()
    return RedirectResponse(url=f"/assets/system/{asset.system_id}", status_code=303)


@router.get("/assets/app/{asset_id}")
def app_asset_page(asset_id: int, request: Request, db: Session = Depends(get_db)):
    require_plugin_enabled(db, "apps_inventory")
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return RedirectResponse(url=f"/assets/system/{asset.system_id}", status_code=303)


@router.post("/assets/import-source")
def assets_import_source_page(db: Session = Depends(get_db)):
    source_type = get_setting_value(
        db,
        "plugin.apps_inventory.source_type",
        get_setting_value(db, "plugin.assets.source_type", get_setting_value(db, "asset_source_type", "file")),
    )
    source = get_setting_value(
        db,
        "plugin.apps_inventory.source",
        get_setting_value(db, "plugin.assets.source", get_setting_value(db, "asset_source", "dev-data/apps-installed.json")),
    )
    if source:
        import_apps_inventory(db=db, inventory=load_asset_source(source_type=source_type, source=source))
        manager = get_plugin_manager()
        import asyncio
        for asset in db.query(Asset).all():
            asyncio.run(manager.export_asset_update(db, asset))
    return RedirectResponse(url="/assets", status_code=303)


@router.post("/assets/refresh-updates")
def assets_refresh_updates_page(db: Session = Depends(get_db)):
    refresh_asset_updates(db)
    manager = get_plugin_manager()
    for asset in db.query(Asset).all():
        import asyncio
        asyncio.run(manager.export_asset_update(db, asset))
    return RedirectResponse(url="/assets", status_code=303)


@router.post("/assets/cleanup-inactive")
def cleanup_inactive_assets(db: Session = Depends(get_db)):
    db.query(Asset).filter(Asset.is_active == False).delete()
    db.commit()
    return RedirectResponse(url="/assets?show_inactive=true", status_code=303)


@router.get("/diagnostics")
def diagnostics_page(request: Request, db: Session = Depends(get_db)):
    plugins = db.query(PluginRecord).order_by(PluginRecord.id).all()
    plugin_rows = [
        {
            "plugin": plugin,
            "configuration_status": "enabled" if is_plugin_enabled(db, plugin.id) else "disabled",
        }
        for plugin in plugins
    ]
    diagnostic_rows = []
    for item in db.query(Diagnostic).order_by(Diagnostic.plugin).all():
        enabled = is_plugin_enabled(db, item.plugin)
        diagnostic_rows.append(
            {
                "item": item,
                "effective_status": item.status if enabled else "disabled",
                "message": item.last_error if enabled else "Plugin is disabled and not running.",
            }
        )
    return render(
        request,
        db,
        "diagnostics.html",
        plugin_rows=plugin_rows,
        datasources=db.query(Datasource).order_by(Datasource.name).all(),
        diagnostic_rows=diagnostic_rows,
        actions=db.query(Action).order_by(Action.timestamp.desc()).limit(20).all(),
    )


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    plugin_setting_groups = get_plugin_manager().plugin_settings(db, get_setting_value(db, "language", "en"))
    plugin_settings_state = {
        setting["key"]: setting["value"]
        for group in plugin_setting_groups
        for setting in group["settings"]
    }
    return render(
        request,
        db,
        "settings.html",
        domain=get_setting_value(db, "domain", ""),
        language_setting=get_setting_value(db, "language", "en"),
        retention_days=get_setting_value(db, "retention_days", "30"),
        live_default=get_setting_value(db, "live_default", "true"),
        theme=get_setting_value(db, "theme", "auto"),
        timezone=get_setting_value(db, "timezone", "auto"),
        asset_source_type=get_setting_value(db, "asset_source_type", "file"),
        asset_source=get_setting_value(db, "asset_source", "dev-data/apps-installed.json"),
        github_token=get_setting_value(db, "github_token", ""),
        action_dry_run=get_setting_value(db, "action_dry_run", "true"),
        plugin_setting_groups=plugin_setting_groups,
        plugin_settings_state=plugin_settings_state,
    )


@router.post("/settings")
async def save_settings(
    request: Request,
    domain: str = Form(""),
    language: str = Form("en"),
    retention_days: str = Form("30"),
    live_default: str = Form("true"),
    theme: str = Form("auto"),
    timezone: str = Form("auto"),
    asset_source_type: str = Form("file"),
    asset_source: str = Form(""),
    github_token: str = Form(""),
    action_dry_run: str = Form("true"),
    db: Session = Depends(get_db),
):
    if language not in {"de", "en"}:
        language = "en"
    for key, value in {
        "domain": domain,
        "language": language,
        "retention_days": retention_days,
        "live_default": live_default,
        "theme": theme,
        "timezone": timezone,
        "asset_source_type": asset_source_type,
        "asset_source": asset_source,
        "github_token": github_token,
        "action_dry_run": action_dry_run,
    }.items():
        save_setting(db, key, value)

    form = await request.form()
    for key, value in form.items():
        if key.startswith("plugin."):
            save_setting(db, key, str(value))
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)
