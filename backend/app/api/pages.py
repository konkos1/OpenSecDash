from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.template_context import build_template_context, get_setting_value
from app.database.dependencies import get_db
from app.models.assets import Asset
from app.models.core import Action, Datasource, Diagnostic, Insight, PluginRecord
from app.models.events import Event
from app.models.settings import Setting
from app.models.systems import System
from app.plugins.assets.importer import import_apps_inventory
from app.plugins.assets.source import load_asset_source
from app.plugins.assets.updates import refresh_asset_updates
from app.services.actions import create_action
from app.services.events import apply_event_filters, import_dev_events

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


templates.env.filters["datetime"] = format_datetime


def save_setting(db: Session, key: str, value: str) -> None:
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting is None:
        db.add(Setting(key=key, value=value))
    else:
        setting.value = value


def today_start() -> datetime:
    now = datetime.utcnow()
    return datetime(now.year, now.month, now.day)


def render(request: Request, db: Session, template: str, **context):
    return templates.TemplateResponse(request=request, name=template, context={**build_template_context(db), **context})


@router.get("/")
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    since = today_start()
    event_count = db.query(Event).filter(Event.event_time >= since).count()
    active_bans = db.query(Event).filter(Event.event_type == "security.ban").count()
    geoblocks = db.query(Event).filter(Event.event_type == "security.geoblock", Event.event_time >= since).count()
    access_events = db.query(Event).filter(Event.event_type.startswith("access."), Event.event_time >= since).count()
    top_countries = (
        db.query(Event.country, func.count(Event.id))
        .filter(Event.country.isnot(None), Event.event_time >= since)
        .group_by(Event.country)
        .order_by(func.count(Event.id).desc())
        .limit(5)
        .all()
    )
    latest_security_events = (
        db.query(Event)
        .filter(Event.event_type.startswith("security."))
        .order_by(Event.event_time.desc())
        .limit(8)
        .all()
    )
    return render(
        request,
        db,
        "dashboard.html",
        event_count=event_count,
        active_bans=active_bans,
        geoblocks=geoblocks,
        torblocks=0,
        access_events=access_events,
        asset_count=db.query(Asset).filter(Asset.is_active == True).count(),
        update_count=db.query(Asset).filter(Asset.update_available == True).count(),
        top_countries=top_countries,
        latest_security_events=latest_security_events,
    )


@router.get("/events")
def events_page(
    request: Request,
    event_type: str | None = None,
    ip: str | None = None,
    country: str | None = None,
    status_code: int | None = None,
    path: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    filters = {"event_type": event_type, "ip": ip, "country": country, "status_code": status_code, "path": path, "q": q}
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    return render(request, db, "events.html", events=events, filters=filters, live_default=get_setting_value(db, "live_default", "true"))


@router.get("/access")
def access_page(request: Request, q: str | None = None, db: Session = Depends(get_db)):
    filters = {"event_type": "access.*", "q": q}
    events = apply_event_filters(db.query(Event), filters).order_by(Event.event_time.desc()).limit(200).all()
    return render(request, db, "access.html", events=events, q=q)


@router.get("/crowdsec")
def crowdsec_page(request: Request, db: Session = Depends(get_db)):
    bans = db.query(Event).filter(Event.event_type == "security.ban").order_by(Event.event_time.desc()).limit(100).all()
    scenarios = (
        db.query(Event.data_json, func.count(Event.id))
        .filter(Event.event_type == "security.ban")
        .group_by(Event.data_json)
        .order_by(func.count(Event.id).desc())
        .limit(10)
        .all()
    )
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
    return RedirectResponse(url=f"/ip/{ip}", status_code=303)


@router.get("/ip/{ip}")
def ip_explorer_page(ip: str, request: Request, db: Session = Depends(get_db)):
    events = db.query(Event).filter(Event.ip == ip).order_by(Event.event_time.desc()).limit(200).all()
    insights = db.query(Insight).filter(Insight.ip == ip).order_by(Insight.timestamp.desc()).limit(50).all()
    counts = {
        "bans": db.query(Event).filter(Event.ip == ip, Event.event_type == "security.ban").count(),
        "geoblocks": db.query(Event).filter(Event.ip == ip, Event.event_type == "security.geoblock").count(),
        "torblocks": db.query(Event).filter(Event.ip == ip, Event.event_type == "security.torblock").count(),
        "access": db.query(Event).filter(Event.ip == ip, Event.event_type.startswith("access.")).count(),
    }
    return render(request, db, "ip.html", ip=ip, events=events, insights=insights, counts=counts)


@router.get("/assets")
def assets_page(request: Request, show_inactive: bool = False, db: Session = Depends(get_db)):
    systems = db.query(System).order_by(System.hostname).all()
    system_rows = []
    for system in systems:
        apps_query = db.query(Asset).filter(Asset.system_id == system.id)
        if not show_inactive:
            apps_query = apps_query.filter(Asset.is_active == True)
        app_count = apps_query.count()
        update_available = (
            apps_query.filter(Asset.update_available == True).count() > 0
        )
        last_seen = (
            apps_query.order_by(Asset.last_seen.desc()).first().last_seen
            if app_count
            else system.last_seen
        )
        system_rows.append(
            {
                "system": system,
                "app_count": app_count,
                "update_available": update_available,
                "last_seen": last_seen,
            }
        )
    return render(request, db, "assets.html", system_rows=system_rows, show_inactive=show_inactive)


@router.get("/assets/system/{system_id}")
def asset_page(system_id: int, request: Request, show_inactive: bool = False, db: Session = Depends(get_db)):
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
    return render(request, db, "asset.html", system=system, apps=apps, events=events, insights=insights, show_inactive=show_inactive)


@router.get("/assets/app/{asset_id}")
def app_asset_page(asset_id: int, request: Request, db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return RedirectResponse(url=f"/assets/system/{asset.system_id}", status_code=303)


@router.post("/assets/import-source")
def assets_import_source_page(db: Session = Depends(get_db)):
    source_type = get_setting_value(db, "asset_source_type", "file")
    source = get_setting_value(db, "asset_source", "dev-data/apps-installed.json")
    if source:
        import_apps_inventory(db=db, inventory=load_asset_source(source_type=source_type, source=source))
    return RedirectResponse(url="/assets", status_code=303)


@router.post("/assets/refresh-updates")
def assets_refresh_updates_page(db: Session = Depends(get_db)):
    refresh_asset_updates(db)
    return RedirectResponse(url="/assets", status_code=303)


@router.post("/assets/cleanup-inactive")
def cleanup_inactive_assets(db: Session = Depends(get_db)):
    db.query(Asset).filter(Asset.is_active == False).delete()
    db.commit()
    return RedirectResponse(url="/assets?show_inactive=true", status_code=303)


@router.post("/dev/import-samples")
def import_samples_page(db: Session = Depends(get_db)):
    import_dev_events(db)
    return RedirectResponse(url="/events", status_code=303)


@router.get("/diagnostics")
def diagnostics_page(request: Request, db: Session = Depends(get_db)):
    return render(
        request,
        db,
        "diagnostics.html",
        plugins=db.query(PluginRecord).order_by(PluginRecord.id).all(),
        datasources=db.query(Datasource).order_by(Datasource.name).all(),
        diagnostics=db.query(Diagnostic).order_by(Diagnostic.plugin).all(),
        actions=db.query(Action).order_by(Action.timestamp.desc()).limit(20).all(),
    )


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
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
        mqtt_enabled=get_setting_value(db, "mqtt_enabled", "false"),
        mqtt_host=get_setting_value(db, "mqtt_host", ""),
        mqtt_port=get_setting_value(db, "mqtt_port", "1883"),
        mqtt_topic_prefix=get_setting_value(db, "mqtt_topic_prefix", "opensecdash"),
    )


@router.post("/settings")
def save_settings(
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
    mqtt_enabled: str = Form("false"),
    mqtt_host: str = Form(""),
    mqtt_port: str = Form("1883"),
    mqtt_topic_prefix: str = Form("opensecdash"),
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
        "mqtt_enabled": mqtt_enabled,
        "mqtt_host": mqtt_host,
        "mqtt_port": mqtt_port,
        "mqtt_topic_prefix": mqtt_topic_prefix,
    }.items():
        save_setting(db, key, value)
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)
