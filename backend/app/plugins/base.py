from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.events import Event
from app.services.events import store_event

PluginCapability = Literal["datasource", "enrichment", "action", "export", "page", "widget", "insight"]
SettingType = Literal["text", "password", "number", "boolean", "select", "file", "url"]


@dataclass(frozen=True)
class PluginSetting:
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
