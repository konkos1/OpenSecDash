import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import configure_logging_from_db, setup_service_logging
from app.core.version import get_app_version

setup_service_logging()

from app.api import actions_router, assets_router, events_router, pages_router, settings_router
from app.database.init_db import init_db
from app.database.migrations import run_auto_migrations_if_enabled, update_migration_diagnostic
from app.core.template_context import build_template_context
from app.database.session import SessionLocal
from app.core.template_context import get_setting_value
from app.models.events import Event
from app.plugins.manager import get_plugin_manager
from app.services.insight_rules import refresh_insight_rules

templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    migration_result = run_auto_migrations_if_enabled()
    init_db()
    manager = get_plugin_manager()
    db = SessionLocal()
    try:
        configure_logging_from_db(db)
        logger.info("OpenSecDash %s starting...", get_app_version())
        if migration_result.get("applied"):
            logger.info(
                "Database migration: schema upgraded from %s to %s",
                migration_result.get("previous"),
                migration_result.get("current"),
            )
        elif migration_result.get("auto_migrate") is False:
            logger.info(
                "Database migration: auto-migration disabled; current=%s head=%s",
                migration_result.get("current"),
                migration_result.get("head"),
            )
        else:
            logger.info("Database migration: schema up to date: %s", migration_result.get("current"))
        update_migration_diagnostic(db)
        refresh_insight_rules(db)
        manager.seed_database(db)
    finally:
        db.close()
    await manager.startup()
    try:
        yield
    finally:
        logger.info("OpenSecDash %s stopping gracefully...", get_app_version())
        await manager.shutdown()


app = FastAPI(title="OpenSecDash", version=get_app_version(), lifespan=lifespan)

app.include_router(settings_router)
app.include_router(events_router)
app.include_router(actions_router)
app.include_router(assets_router)
app.include_router(pages_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


def wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return request.url.path.startswith("/api/") or ("application/json" in accept and "text/html" not in accept)


def render_error_page(
    request: Request,
    status_code: int,
    title_key: str,
    message_key: str,
    details: list[dict[str, str]] | None = None,
):
    db = SessionLocal()
    try:
        context = build_template_context(db)
    finally:
        db.close()
    title = context["t"](title_key)  # type: ignore[index,operator]
    message = context["t"](message_key)  # type: ignore[index,operator]
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        status_code=status_code,
        context={**context, "title": title, "message": message, "details": details or []},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if wants_json(request):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    details = []
    for error in exc.errors():
        location = error.get("loc", [])
        field = ".".join(str(part) for part in location if part not in {"query", "path", "body", "form"}) or "request"
        details.append({"field": field, "message": str(error.get("msg", "Invalid value"))})
    return render_error_page(request, 400, "error.invalid_input.title", "error.invalid_input.message", details)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if wants_json(request):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    title_key = "error.not_found.title" if exc.status_code == 404 else "error.generic.title"
    message_key = "error.not_found.message" if exc.status_code == 404 else "error.generic.message"
    return render_error_page(request, exc.status_code, title_key, message_key)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if wants_json(request):
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    return render_error_page(request, 500, "error.generic.title", "error.generic.message")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    init_db()
    return {"status": "ready"}


def _websocket_poll_state() -> tuple[bool, int]:
    """One poll iteration's DB reads, sharing a single session.

    Runs via ``asyncio.to_thread``: each connected client polls once per
    second, and these blocking queries must not run on the event loop itself -
    with a few open tabs that would otherwise add several loop-freezing DB
    calls every second, exactly when the app is busiest.
    """
    db = SessionLocal()
    try:
        enabled = any(
            get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"
            for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"]
        )
        return enabled, int(db.query(func.max(Event.id)).scalar() or 0)
    finally:
        db.close()


@app.websocket("/ws/events")
async def events_websocket(websocket: WebSocket) -> None:
    enabled, last_seen_id = await asyncio.to_thread(_websocket_poll_state)
    if not enabled:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    await websocket.send_json({"type": "connected", "last_event_id": last_seen_id})

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1)
            except TimeoutError:
                pass

            enabled, current_id = await asyncio.to_thread(_websocket_poll_state)
            if not enabled:
                await websocket.close(code=1008)
                return

            if current_id > last_seen_id:
                last_seen_id = current_id
                await websocket.send_json({"type": "events_changed", "last_event_id": current_id})
    except WebSocketDisconnect:
        return
    except RuntimeError:
        return
