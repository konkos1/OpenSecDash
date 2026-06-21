from fastapi import APIRouter, Depends, Request
from fastapi import Form
from fastapi import HTTPException

from fastapi.templating import Jinja2Templates

from fastapi.responses import RedirectResponse

from sqlalchemy.orm import Session

from app.core.i18n import translate
from app.core.language import get_language
from app.core.template_context import build_template_context

from app.models.assets import Asset
from app.models.systems import System
from app.models.settings import Setting
from app.models.events import Event

from app.database.dependencies import get_db

from app.plugins.assets.importer import import_apps_inventory
from app.plugins.assets.source import load_asset_source
from app.plugins.assets.updates import refresh_asset_updates


router = APIRouter(
    tags=["pages"],
)


templates = Jinja2Templates(
    directory="app/templates"
)


def get_setting_value(db: Session, key: str, default: str = "") -> str:
    setting = db.query(Setting).filter(Setting.key == key).first()

    if setting is None:
        return default

    return setting.value


###############################################################################
# ROUTER
###############################################################################

@router.get("/")
def dashboard_page(
    request: Request,
    db: Session = Depends(get_db),
):
    context = build_template_context(db)

    event_count = db.query(Event).count()

    system_count = db.query(System).count()

    asset_count = (
        db.query(Asset)
        .filter(Asset.is_active == True)
        .count()
    )

    inactive_asset_count = (
        db.query(Asset)
        .filter(Asset.is_active == False)
        .count()
    )

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            **context,
            "event_count": event_count,
            "system_count": system_count,
            "asset_count": asset_count,
            "inactive_asset_count": inactive_asset_count,
        },
    )


@router.get("/events")
def events_page(
    request: Request,
    db: Session = Depends(get_db),
):
    context = build_template_context(db)

    events = (
        db.query(Event)
        .order_by(Event.timestamp.desc())
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="events.html",
        context={
            **context,
            "events": events,
        },
    )


@router.get("/assets")
def assets_page(
    request: Request,
    show_inactive: bool = False,
    db: Session = Depends(get_db),
):
    query = (
        db.query(Asset)
        .join(System, Asset.system_id == System.id)
    )

    if not show_inactive:
        query = query.filter(Asset.is_active == True)

    assets = (
        query
        .order_by(System.hostname, Asset.name)
        .all()
    )

    context = build_template_context(db)

    return templates.TemplateResponse(
        request=request,
        name="assets.html",
        context={
            **context,
            "assets": assets,
            "show_inactive": show_inactive,
        },
    )


@router.get("/assets/{asset_id}")
def asset_page(
    asset_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    asset = (
        db.query(Asset)
        .filter(Asset.id == asset_id)
        .first()
    )

    if asset is None:
        raise HTTPException(
            status_code=404,
            detail="Asset not found",
        )

    context = build_template_context(db)

    return templates.TemplateResponse(
        request=request,
        name="asset.html",
        context={
            **context,
            "asset": asset,
        },
    )


@router.post("/assets/import-source")
def assets_import_source_page(
    db: Session = Depends(get_db),
):
    source_type = get_setting_value(
        db,
        "asset_source_type",
        "url",
    )

    source = get_setting_value(
        db,
        "asset_source",
        "",
    )

    if source:
        inventory = load_asset_source(
            source_type=source_type,
            source=source,
        )

        import_apps_inventory(
            db=db,
            inventory=inventory,
        )

    return RedirectResponse(
        url="/assets",
        status_code=303,
    )


@router.post("/assets/refresh-updates")
def assets_refresh_updates_page(
    db: Session = Depends(get_db),
):
    refresh_asset_updates(db)

    return RedirectResponse(
        url="/assets",
        status_code=303,
    )


@router.post("/assets/cleanup-inactive")
def cleanup_inactive_assets(
    db: Session = Depends(get_db),
):
    (
        db.query(Asset)
        .filter(Asset.is_active == False)
        .delete()
    )

    db.commit()

    return RedirectResponse(
        url="/assets?show_inactive=true",
        status_code=303,
    )


@router.get("/settings")
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
):
    context = build_template_context(db)

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            **context,
            "domain": get_setting_value(db, "domain", ""),
            "language_setting": get_setting_value(db, "language", "de"),
            "asset_source_type": get_setting_value(db, "asset_source_type", "url"),
            "asset_source": get_setting_value(db, "asset_source", ""),
            "github_token": get_setting_value(db, "github_token", ""),
        },
    )


@router.post("/settings")
def save_settings(
    domain: str = Form(""),
    language: str = Form("de"),
    asset_source_type: str = Form("url"),
    asset_source: str = Form(""),
    github_token: str = Form(""),
    db: Session = Depends(get_db),
):
    if language not in {"de", "en"}:
        language = "de"

    if asset_source_type not in {"url", "file"}:
        asset_source_type = "url"

    for key, value in {
        "domain": domain,
        "language": language,
        "asset_source_type": asset_source_type,
        "asset_source": asset_source,
        "github_token": github_token,
    }.items():
        setting = db.query(Setting).filter(Setting.key == key).first()

        if setting is None:
            setting = Setting(
                key=key,
                value=value,
            )
            db.add(setting)
        else:
            setting.value = value

    db.commit()

    return RedirectResponse(
        url="/settings",
        status_code=303,
    )
