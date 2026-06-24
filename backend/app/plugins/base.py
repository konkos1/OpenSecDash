from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.events import Event
from app.services.events import store_event

# Keep this module intentionally dependency-light: external plugins import it as
# their public API surface. Changes here should be backwards compatible or paired
# with an ADR/plugin API version bump.
PluginCapability = Literal["datasource", "enrichment", "action", "export", "page", "widget", "insight"]
SettingType = Literal["text", "password", "number", "boolean", "select", "file", "url"]


@dataclass(frozen=True)
class PluginSetting:
    """Declarative setting metadata rendered automatically on Settings page.

    Plugins define labels/help as translation keys so the backend core does not
    need plugin-specific UI code. ``visible_if`` references another setting key
    local to the same plugin.
    """
    key: str
    label_key: str
    help_key: str
    type: SettingType = "text"
    default: str = ""
    options: list[tuple[str, str]] = field(default_factory=list)
    visible_if: tuple[str, str] | None = None


@dataclass(frozen=True)
class PluginMetadata:
    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    api_version: str = "1"
    capabilities: list[PluginCapability] = field(default_factory=list)


class PluginContext:
    """Runtime services exposed to plugins.

    Keep plugin side effects behind this context where possible. That makes
    plugin behavior easier to test later with a fake ``PluginContext``.
    """

    def __init__(
        self,
        db: Session,
        settings: dict[str, str],
        asset_exporter: Callable[[Session, Any, bool], Awaitable[None]] | None = None,
        manual_export: bool = False,
    ) -> None:
        self.db = db
        self.settings = settings
        self.manual_export = manual_export
        self._asset_exporter = asset_exporter

    def get(self, key: str, default: str = "") -> str:
        return self.settings.get(key, default)

    def emit_event(self, **values: Any) -> Event:
        return store_event(self.db, **values)

    async def export_asset_update(self, asset: Any, manual: bool = False) -> None:
        if self._asset_exporter is not None:
            await self._asset_exporter(self.db, asset, manual)


class Plugin:
    """Base plugin contract.

    Subclasses override only the hooks they need. The manager calls these hooks
    from isolated background loops and records failures as diagnostics.
    """

    metadata: PluginMetadata
    settings: list[PluginSetting] = []
    locales: dict[str, dict[str, str]] = {"en": {}, "de": {}}

    async def startup(self, context: PluginContext) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def health(self, context: PluginContext) -> dict[str, str]:
        return {"status": "healthy"}


class DatasourcePlugin(Plugin):
    async def collect(self, context: PluginContext) -> Iterable[dict[str, Any]]:
        return []


class PeriodicPlugin(Plugin):
    async def tick(self, context: PluginContext) -> None:
        return None


class ActionPlugin(Plugin):
    async def execute(
        self,
        context: PluginContext,
        action_type: str,
        target: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any] | None:
        raise NotImplementedError


class ExportPlugin(Plugin):
    async def export_event(self, context: PluginContext, event: Event) -> None:
        return None

    async def export_asset(self, context: PluginContext, asset: Any) -> None:
        return None
