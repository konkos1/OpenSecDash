from app.api.actions import router as actions_router
from app.api.action_forms import router as action_forms_router
from app.api.assets import router as assets_router
from app.api.auth import router as auth_router
from app.api.events import router as events_router
from app.api.instance import router as instance_router
from app.api.pages import router as pages_router
from app.api.settings import router as settings_router

__all__ = [
    "actions_router",
    "action_forms_router",
    "assets_router",
    "auth_router",
    "events_router",
    "instance_router",
    "pages_router",
    "settings_router",
]
