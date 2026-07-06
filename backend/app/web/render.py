from fastapi import Request
from sqlalchemy.orm import Session

from app.core.template_context import build_template_context
from app.web.templates import templates


def render(request: Request, db: Session, template: str, **context):
    # All page routes (core and plugin) go through this helper so global
    # template context (i18n, feature flags, settings, nav) stays consistent
    # and easy to exercise in tests.
    return templates.TemplateResponse(request=request, name=template, context={**build_template_context(db), **context})
