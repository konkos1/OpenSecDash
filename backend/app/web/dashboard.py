from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import json
import logging
from typing import Any, Iterable, Literal

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.plugins.manager import get_plugin_manager


WidgetType = Literal["counter", "table", "feed", "trend", "map"]
WidgetSection = Literal["security", "activity", "assets", "trends", "feed"]

_ALLOWED_TYPES = {"counter", "table", "feed", "trend", "map"}
_ALLOWED_SECTIONS = {"security", "activity", "assets", "trends", "feed"}
_SECTION_ORDER = {"security": 0, "activity": 1, "assets": 2, "trends": 3, "feed": 4}
_DEFAULT_TYPE_ORDER = {"counter": 0, "table": 1, "map": 2, "trend": 3, "feed": 4}

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
    visible: bool = True


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
    if not isinstance(widget.visible, bool):
        return False
    if not isinstance(widget.order, int) or isinstance(widget.order, bool):
        return False
    if widget.value is not None and (not isinstance(widget.value, int) or isinstance(widget.value, bool)):
        return False
    if widget.empty_key is not None and (not isinstance(widget.empty_key, str) or not widget.empty_key):
        return False

    def valid_href(value: object) -> bool:
        return value is None or (
            isinstance(value, str)
            and value.startswith("/")
            and not value.startswith("//")
        )

    def required_href(value: object) -> bool:
        return isinstance(value, str) and valid_href(value)

    if not valid_href(widget.href):
        return False

    if not isinstance(widget.rows, (list, tuple)):
        return False
    if widget.type == "table":
        for row in widget.rows:
            if not isinstance(row, Mapping):
                return False
            if not isinstance(row.get("label"), str):
                return False
            if not isinstance(row.get("value"), int) or isinstance(row.get("value"), bool):
                return False
            if not required_href(row.get("href")):
                return False
    elif widget.type == "feed":
        for row in widget.rows:
            if not isinstance(row, Mapping):
                return False
            if not isinstance(row.get("time"), str) and not hasattr(row.get("time"), "strftime"):
                return False
            if not isinstance(row.get("type"), str) or not isinstance(row.get("ip"), str):
                return False
            if not required_href(row.get("href")):
                return False
            if "country" in row and not isinstance(row.get("country"), str):
                return False
    elif widget.type == "trend":
        for row in widget.rows:
            if not isinstance(row, Mapping):
                return False
            if not isinstance(row.get("bucket"), str):
                return False
            value = row.get("value")
            if not isinstance(value, int) or isinstance(value, bool):
                return False
            if value < 0:
                return False
    elif widget.type == "map":
        if widget.id != "core.country_heatmap":
            return False
        for row in widget.rows:
            if not isinstance(row, Mapping):
                return False
            if not isinstance(row.get("country"), str):
                return False
            if not isinstance(row.get("count"), int) or isinstance(row.get("count"), bool):
                return False
            for key in ("x", "y", "radius"):
                if not isinstance(row.get(key), (int, float)) or isinstance(row.get(key), bool):
                    return False
    return True


def load_dashboard_layout(db: Session) -> list[dict[str, object]]:
    """Load valid layout entries from the migrations-free settings store."""
    raw_layout = get_setting_value(db, "ui.dashboard_layout", "")
    if not raw_layout:
        return []
    try:
        payload = json.loads(raw_layout)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []

    entries: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        widget_id = item.get("id")
        visible = item.get("visible")
        if isinstance(widget_id, str) and widget_id and isinstance(visible, bool):
            entries.append({"id": widget_id, "visible": visible})
    return entries


def apply_layout(widgets: list[DashboardWidget], layout: list[dict[str, object]]) -> list[DashboardWidget]:
    """Apply stored order and visibility while tolerating stale or new widgets."""
    natural_widgets = sorted(
        widgets,
        key=lambda item: (_DEFAULT_TYPE_ORDER[item.type], _SECTION_ORDER[item.section], item.order, item.id),
    )
    widgets_by_id = {widget.id: widget for widget in natural_widgets}
    layout_visibility: dict[str, bool] = {}
    for entry in layout:
        widget_id = entry.get("id")
        visible = entry.get("visible")
        if (
            isinstance(widget_id, str)
            and widget_id in widgets_by_id
            and widget_id not in layout_visibility
            and isinstance(visible, bool)
        ):
            layout_visibility[widget_id] = visible

    ordered: list[DashboardWidget] = []
    seen_ids: set[str] = set()
    for widget_id, visible in layout_visibility.items():
        ordered.append(replace(widgets_by_id[widget_id], visible=visible))
        seen_ids.add(widget_id)
    for widget in natural_widgets:
        if widget.id not in seen_ids:
            ordered.append(replace(widget, visible=True))
    return ordered


def collect_dashboard_widgets(db: Session, core_widgets: Iterable[DashboardWidget]) -> list[DashboardWidget]:
    """Collect, validate, deduplicate, and deterministically order descriptors."""
    widgets: list[DashboardWidget] = []
    seen_ids: set[str] = set()

    def add_widgets(candidates: Iterable[DashboardWidget]) -> None:
        for widget in candidates:
            if not validate_widget(widget):
                logger.warning("Skipping invalid dashboard widget %s", widget.id)
                continue
            if widget.id in seen_ids:
                continue
            seen_ids.add(widget.id)
            widgets.append(widget)

    add_widgets(core_widgets)
    for plugin in get_plugin_manager().plugins.values():
        try:
            add_widgets(plugin.dashboard_widgets(db))
        except Exception:
            logger.warning("Dashboard widget hook failed for plugin %s", plugin.metadata.id, exc_info=True)
    return sorted(widgets, key=lambda item: (_SECTION_ORDER[item.section], item.order, item.id))
