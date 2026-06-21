from app.api.actions import router as actions_router
from app.api.assets import router as assets_router
from app.api.events import router as events_router
from app.api.pages import router as pages_router
from app.api.settings import router as settings_router

__all__ = [
    "actions_router",
    "assets_router",
    "events_router",
    "pages_router",
    "settings_router",
]
