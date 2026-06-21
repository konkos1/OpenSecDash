from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.api import actions_router, assets_router, events_router, pages_router, settings_router
from app.database.init_db import init_db

app = FastAPI(title="OpenSecDash")

app.include_router(settings_router)
app.include_router(events_router)
app.include_router(actions_router)
app.include_router(assets_router)
app.include_router(pages_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    init_db()
    return {"status": "ready"}


@app.websocket("/ws/events")
async def events_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        await websocket.send_json({"type": "connected", "message": "OpenSecDash live mode ready"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
