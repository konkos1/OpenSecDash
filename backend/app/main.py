from fastapi import FastAPI

from fastapi.staticfiles import StaticFiles

from app.api import assets_router
from app.api import events_router
from app.api import pages_router
from app.api import settings_router

app = FastAPI(
    title="OpenSecDash",
)

app.include_router(settings_router)
app.include_router(events_router)
app.include_router(assets_router)
app.include_router(pages_router)


app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static",
)
