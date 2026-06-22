from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value
from app.database.session import SessionLocal
from app.models.core import Datasource, Diagnostic, PluginRecord
from app.models.settings import Setting
from app.plugins.base import ActionPlugin, DatasourcePlugin, ExportPlugin, PeriodicPlugin, Plugin, PluginContext, PluginSetting
from app.core.time import utc_now


class PluginManager:
    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.plugins: dict[str, Plugin] = {}
        self.tasks: list[asyncio.Task] = []

    def discover(self) -> None:
        self.plugins.clear()
        if not self.plugin_dir.exists():
            return
        for plugin_py in sorted(self.plugin_dir.glob("*/plugin.py")):
            module = self._load_module(plugin_py)
            plugin_class = getattr(module, "Plugin", None)
            if plugin_class is None:
                continue
            plugin: Plugin = plugin_class()
            self.plugins[plugin.metadata.id] = plugin

    def _load_module(self, path: Path) -> ModuleType:
        module_name = f"opensecdash_external_plugin_{path.parent.name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def seed_database(self, db: Session) -> None:
        for plugin in self.plugins.values():
            meta = plugin.metadata
            record = db.query(PluginRecord).filter(PluginRecord.id == meta.id).first()
            if record is None:
                db.add(
                    PluginRecord(
                        id=meta.id,
                        name=meta.name,
                        version=meta.version,
                        description=meta.description,
                        author=meta.author,
                        api_version=meta.api_version,
                        capabilities=meta.capabilities,
                        status="healthy",
                    )
                )
            else:
                record.name = meta.name
                record.version = meta.version
                record.description = meta.description
                record.author = meta.author
                record.api_version = meta.api_version
                record.capabilities = meta.capabilities

            diagnostic = db.query(Diagnostic).filter(Diagnostic.plugin == meta.id, Diagnostic.component == "plugin").first()
            if diagnostic is None:
                db.add(Diagnostic(plugin=meta.id, component="plugin", status="healthy"))

            if isinstance(plugin, DatasourcePlugin):
                datasource = db.query(Datasource).filter(Datasource.plugin_id == meta.id).first()
                if datasource is None:
                    db.add(
                        Datasource(
                            name=meta.name,
                            plugin_id=meta.id,
                            enabled=False,
                            source_type="logfile",
                            config={},
                            status="disabled",
                        )
                    )
                else:
                    datasource.name = meta.name
                    datasource.source_type = "logfile"

            for setting in plugin.settings:
                key = self.setting_key(meta.id, setting.key)
                existing = db.query(Setting).filter(Setting.key == key).first()
                if existing is None:
                    db.add(Setting(key=key, value=setting.default))
        db.commit()

    @staticmethod
    def setting_key(plugin_id: str, key: str) -> str:
        return f"plugin.{plugin_id}.{key}"

    def plugin_settings(self, db: Session, language: str) -> list[dict[str, Any]]:
        groups = []
        for plugin in self.plugins.values():
            settings = []
            locale = plugin.locales.get(language, plugin.locales.get("en", {}))
            fallback = plugin.locales.get("en", {})
            has_enabled_toggle = any(setting.key == "enabled" for setting in plugin.settings)
            plugin_enabled = get_setting_value(db, self.setting_key(plugin.metadata.id, "enabled"), "false") == "true"
            for setting in plugin.settings:
                full_key = self.setting_key(plugin.metadata.id, setting.key)
                value = get_setting_value(db, full_key, setting.default)
                visible_if = setting.visible_if
                if visible_if is None and has_enabled_toggle and setting.key != "enabled":
                    visible_if = ("enabled", "true")
                error = None
                if setting.type == "file" and plugin_enabled and value and not Path(value).exists():
                    error = (
                        f"File not found: {value}"
                        if language == "en"
                        else f"Datei nicht gefunden: {value}"
                    )
                settings.append(
                    {
                        "key": full_key,
                        "short_key": setting.key,
                        "value": value,
                        "type": setting.type,
                        "label": locale.get(setting.label_key, fallback.get(setting.label_key, setting.label_key)),
                        "help": locale.get(setting.help_key, fallback.get(setting.help_key, setting.help_key)),
                        "error": error,
                        "options": [
                            (value, locale.get(label_key, fallback.get(label_key, label_key)))
                            for value, label_key in setting.options
                        ],
                        "visible_if": (
                            {
                                "key": self.setting_key(plugin.metadata.id, visible_if[0]),
                                "value": visible_if[1],
                            }
                            if visible_if
                            else None
                        ),
                    }
                )
            if settings:
                groups.append({"id": plugin.metadata.id, "name": plugin.metadata.name, "settings": settings})
        return groups

    def context(self, db: Session, plugin: Plugin) -> PluginContext:
        values = {}
        for setting in plugin.settings:
            values[setting.key] = get_setting_value(db, self.setting_key(plugin.metadata.id, setting.key), setting.default)
        return PluginContext(db, values, self.export_asset_update)

    async def startup(self) -> None:
        for plugin in self.plugins.values():
            if isinstance(plugin, DatasourcePlugin):
                self.tasks.append(asyncio.create_task(self._datasource_loop(plugin), name=f"plugin-datasource-{plugin.metadata.id}"))
            if isinstance(plugin, PeriodicPlugin):
                self.tasks.append(asyncio.create_task(self._periodic_loop(plugin), name=f"plugin-periodic-{plugin.metadata.id}"))

    async def shutdown(self) -> None:
        if not self.tasks:
            return
        for task in self.tasks:
            task.cancel()
        done, pending = await asyncio.wait(self.tasks, timeout=5)
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        for task in pending:
            task.cancel()
        self.tasks.clear()

    async def _datasource_loop(self, plugin: DatasourcePlugin) -> None:
        while True:
            db = SessionLocal()
            try:
                ctx = self.context(db, plugin)
                interval = int(ctx.get("poll_interval", "5") or "5")
                enabled = ctx.get("enabled", "false").lower() == "true"
                self._update_datasource(db, plugin.metadata.id, enabled, "running" if enabled else "disabled", None, 0)
                if enabled:
                    events = await plugin.collect(ctx)
                    processed = 0
                    last_event_at = None
                    for event in events:
                        stored = ctx.emit_event(**event)
                        processed += 1
                        last_event_at = stored.event_time
                    db.commit()
                    self._update_diagnostic(db, plugin.metadata.id, "healthy", None)
                    self._update_datasource(db, plugin.metadata.id, True, "healthy", None, processed, last_event_at)
                    db.commit()
                await asyncio.sleep(max(interval, 1))
            except asyncio.CancelledError:
                db.close()
                raise
            except Exception as exc:
                self._update_diagnostic(db, plugin.metadata.id, "error", str(exc))
                self._update_datasource(db, plugin.metadata.id, True, "error", str(exc), 0)
                db.commit()
                await asyncio.sleep(10)
            finally:
                db.close()

    async def _periodic_loop(self, plugin: PeriodicPlugin) -> None:
        while True:
            db = SessionLocal()
            try:
                ctx = self.context(db, plugin)
                enabled = ctx.get("enabled", "false").lower() == "true"
                if enabled:
                    await plugin.tick(ctx)
                    db.commit()
                    self._update_diagnostic(db, plugin.metadata.id, "healthy", None)
                    db.commit()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                db.close()
                raise
            except Exception as exc:
                self._update_diagnostic(db, plugin.metadata.id, "error", str(exc))
                db.commit()
                await asyncio.sleep(60)
            finally:
                db.close()

    def _update_datasource(
        self,
        db: Session,
        plugin_id: str,
        enabled: bool,
        status: str,
        error: str | None,
        processed: int,
        last_event_at: Any | None = None,
    ) -> None:
        datasource = db.query(Datasource).filter(Datasource.plugin_id == plugin_id).first()
        if datasource is None:
            plugin = self.plugins.get(plugin_id)
            datasource = Datasource(
                name=plugin.metadata.name if plugin else plugin_id,
                plugin_id=plugin_id,
                source_type="logfile",
                config={},
            )
            db.add(datasource)
        datasource.enabled = enabled
        datasource.status = status
        datasource.last_error = error
        datasource.last_run_at = utc_now().replace(tzinfo=None)
        datasource.events_processed += processed
        if last_event_at is not None:
            datasource.last_event_at = last_event_at

    def _update_diagnostic(self, db: Session, plugin_id: str, status: str, error: str | None) -> None:
        diagnostic = db.query(Diagnostic).filter(Diagnostic.plugin == plugin_id, Diagnostic.component == "plugin").first()
        if diagnostic is None:
            diagnostic = Diagnostic(plugin=plugin_id, component="plugin")
            db.add(diagnostic)
        diagnostic.status = status
        diagnostic.last_error = error

    async def execute_action(self, db: Session, action_type: str, target: str, parameters: dict[str, Any]) -> dict[str, Any] | None:
        for plugin in self.plugins.values():
            if isinstance(plugin, ActionPlugin):
                ctx = self.context(db, plugin)
                result = await plugin.execute(ctx, action_type, target, parameters)
                if result is not None:
                    return result
        return None

    async def export_asset_update(self, db: Session, asset: Any) -> None:
        for plugin in self.plugins.values():
            if isinstance(plugin, ExportPlugin):
                ctx = self.context(db, plugin)
                enabled = ctx.get("enabled", "false").lower() == "true"
                if enabled:
                    await plugin.export_asset(ctx, asset)


_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        root = Path(__file__).resolve().parents[3]
        _manager = PluginManager(root / "plugins")
        _manager.discover()
    return _manager
