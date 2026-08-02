from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core import plugin_registry
from app.core.i18n import translate
from app.core.template_context import get_setting_value
from app.models.core import InsightRule


@dataclass(frozen=True)
class InsightDefinition:
    type: str
    title: str
    level: str
    event_type_requirements: tuple[tuple[str, ...], ...]
    owner_plugin_id: str | None = None


@dataclass(frozen=True)
class InsightAvailability:
    available: bool
    missing_owner_plugin_id: str | None = None
    missing_event_type_groups: tuple[tuple[str, ...], ...] = ()


BUILTIN_INSIGHT_DEFINITIONS = (
    InsightDefinition(
        type="geoblock_denied_request",
        title="Request denied by GeoBlock",
        level="high",
        event_type_requirements=(("security.geoblock",),),
    ),
    InsightDefinition(
        type="security_ban_observed",
        title="Security ban observed",
        level="high",
        event_type_requirements=(("security.ban",),),
    ),
    InsightDefinition(
        type="manual_security_ban",
        title="Manual security ban",
        level="high",
        event_type_requirements=(("security.ban.manual",),),
    ),
    InsightDefinition(
        type="blocked_request",
        title="Blocked request detected",
        level="high",
        event_type_requirements=(("security.geoblock",), ("access.error",)),
    ),
    InsightDefinition(
        type="ban_after_access",
        title="CrowdSec ban after access errors",
        level="high",
        event_type_requirements=(
            ("security.ban", "security.ban.manual"),
            ("access.error",),
        ),
    ),
)


def insight_definitions(db: Session) -> dict[str, InsightDefinition]:
    """Return built-in and active declarative Insight definitions by type."""
    definitions = {definition.type: definition for definition in BUILTIN_INSIGHT_DEFINITIONS}
    rows = (
        db.query(InsightRule)
        .filter(InsightRule.is_active == True)  # noqa: E712
        .order_by(InsightRule.rule_id)
        .all()
    )
    for row in rows:
        owner_plugin_id = row.source.removeprefix("plugin:") if row.source.startswith("plugin:") else None
        definitions[row.rule_id] = InsightDefinition(
            type=row.rule_id,
            title=row.title,
            level=row.level,
            event_type_requirements=(tuple(str(value) for value in (row.event_types or [])),),
            owner_plugin_id=owner_plugin_id,
        )
    return definitions


def _plugin_enabled(db: Session, plugin_id: str) -> bool:
    return (
        plugin_registry.is_registered(plugin_id)
        and get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"
    )


def insight_availability(db: Session, definition: InsightDefinition) -> InsightAvailability:
    """Return whether enabled plugins can produce every input required by an Insight."""
    missing_owner = None
    if definition.owner_plugin_id is not None and not _plugin_enabled(db, definition.owner_plugin_id):
        missing_owner = definition.owner_plugin_id

    missing_groups = []
    for event_types in definition.event_type_requirements:
        producers = {
            plugin_id
            for event_type in event_types
            for plugin_id in plugin_registry.ids_producing_event_type(event_type)
        }
        if not any(_plugin_enabled(db, plugin_id) for plugin_id in producers):
            missing_groups.append(event_types)

    return InsightAvailability(
        available=missing_owner is None and not missing_groups,
        missing_owner_plugin_id=missing_owner,
        missing_event_type_groups=tuple(missing_groups),
    )


def available_insight_types(db: Session) -> set[str]:
    return {
        insight_type
        for insight_type, definition in insight_definitions(db).items()
        if insight_availability(db, definition).available
    }


def localized_insight_title(definition: InsightDefinition, language: str) -> str:
    key = f"insight.title.{definition.type}"
    localized = translate(key, language)
    return definition.title if localized == key else localized


def insight_unavailable_reason(db: Session, definition: InsightDefinition, language: str) -> str:
    availability = insight_availability(db, definition)
    reasons = []
    if availability.missing_owner_plugin_id is not None:
        reasons.append(
            translate("notifications.requires_plugin", language).format(
                plugin=plugin_registry.plugin_name(availability.missing_owner_plugin_id)
            )
        )
    if availability.missing_event_type_groups:
        event_types = ", ".join(" / ".join(group) for group in availability.missing_event_type_groups)
        reasons.append(translate("notifications.requires_event_producer", language).format(event_types=event_types))
    return "; ".join(reasons)
