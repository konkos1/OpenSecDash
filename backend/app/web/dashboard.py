from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Iterable, Literal

from sqlalchemy.orm import Session


WidgetType = Literal["counter", "table", "feed", "trend"]
WidgetSection = Literal["security", "activity", "assets", "trends", "feed"]

_ALLOWED_TYPES = {"counter", "table", "feed", "trend"}
_ALLOWED_SECTIONS = {"security", "activity", "assets", "trends", "feed"}
_SECTION_ORDER = {"security": 0, "activity": 1, "assets": 2, "trends": 3, "feed": 4}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardWidget:
    """Validated data rendered by a core dashboard widget template."""

    id: str
    type: WidgetType
    section: WidgetSection
    title_key: str
    order: int = 100
    value: int | None = None
    href: str | None = None
    delta: dict[str, Any] | None = None
    rows: tuple[dict[str, Any], ...] = ()
    empty_key: str | None = None


def validate_widget(widget: DashboardWidget) -> bool:
    """Reject malformed or unsafe widget descriptors."""
    if not isinstance(widget.type, str) or not isinstance(widget.section, str):
        return False
    if widget.type not in _ALLOWED_TYPES or widget.section not in _ALLOWED_SECTIONS:
        return False
    if not isinstance(widget.id, str) or not widget.id:
        return False
    if not isinstance(widget.title_key, str) or not widget.title_key:
        return False
    if widget.href is not None and (
        not isinstance(widget.href, str)
        or not widget.href.startswith("/")
        or widget.href.startswith("//")
    ):
        return False
    return True


def collect_dashboard_widgets(db: Session, core_widgets: Iterable[DashboardWidget]) -> list[DashboardWidget]:
    """Validate, deduplicate, and deterministically order core descriptors."""
    del db  # The session is used by plugin providers starting in phase 2.
    widgets: list[DashboardWidget] = []
    seen_ids: set[str] = set()
    for widget in core_widgets:
        if not validate_widget(widget):
            logger.warning("Skipping invalid dashboard widget %s", widget.id)
            continue
        if widget.id in seen_ids:
            continue
        seen_ids.add(widget.id)
        widgets.append(widget)
    return sorted(widgets, key=lambda item: (_SECTION_ORDER[item.section], item.order, item.id))
