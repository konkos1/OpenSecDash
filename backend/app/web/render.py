from fastapi import Request
from sqlalchemy.orm import Session

from app.core.template_context import build_template_context
from app.web.templates import templates


def render(request: Request, db: Session, template: str, **context):
    # All page routes (core and plugin) go through this helper so global
    # template context (i18n, feature flags, settings, nav) stays consistent
    # and easy to exercise in tests.
    context.setdefault("current_user", getattr(request.state, "user", None))
    context.setdefault("auth_enabled", getattr(request.state, "auth_enabled", False))
    # Decided once per request in the auth middleware, so no page has to load
    # the onboarding state itself to show the permanent security prompt or tell
    # the break-glass warning apart from a plain disabled state.
    context.setdefault("auth_break_glass", getattr(request.state, "auth_break_glass", False))
    context.setdefault("onboarding_review_required", getattr(request.state, "onboarding_review_required", False))
    current_user = context["current_user"]
    context.setdefault("can_operate", current_user is None or current_user.role in ("operator", "admin"))
    context.setdefault("can_admin", current_user is None or current_user.role == "admin")
    return templates.TemplateResponse(request=request, name=template, context={**build_template_context(db, current_user), **context})
