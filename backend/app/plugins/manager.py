from __future__ import annotations

import asyncio
import importlib.util
import logging
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


logger = logging.getLogger(__name__)


class PluginManager:
    """Discovers and orchestrates external plugins.

    External plugins live outside ``app/`` by design. The manager owns lifecycle,
    settings lookup, diagnostics, and cross-plugin calls so plugins can stay
    small and ADR-compliant.
    """

    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.plugins: dict[str, Plugin] = {}
        self.tasks: list[asyncio.Task] = []

    def discover(self) -> None:
        # Discovery is file-system based to keep packaging simple for community
        # plugins: each plugin is a directory with a ``plugin.py`` exposing
        # ``Plugin``. Avoid importing arbitrary helper files here.
        self.plugins.clear()
        if not self.plugin_dir.exists():
            logger.warning("Plugin directory does not exist: %s", self.plugin_dir)
            return
        for plugin_py in sorted(self.plugin_dir.glob("*/plugin.py")):
            module = self._load_module(plugin_py)
            plugin_class = getattr(module, "Plugin", None)
            if plugin_class is None:
                continue
            plugin: Plugin = plugin_class()
            self.plugins[plugin.metadata.id] = plugin
            logger.debug("Discovered plugin %s from %s", plugin.metadata.id, plugin_py)

    def _load_module(self, path: Path) -> ModuleType:
        module_name = f"opensecdash_external_plugin_{path.parent.name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def seed_database(self, db: Session) -> None:
        # Persist plugin metadata and default settings so the UI can render
        # plugin state even before a plugin has run successfully.
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
        logger.debug("Seeded %d plugin records", len(self.plugins))

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

    def context(self, db: Session, plugin: Plugin, manual_export: bool = False) -> PluginContext:
        values = {}
        for setting in plugin.settings:
            values[setting.key] = get_setting_value(db, self.setting_key(plugin.metadata.id, setting.key), setting.default)
        return PluginContext(db, values, self.export_asset_update, manual_export)

    async def startup(self) -> None:
        logger.info("Starting %d plugin task groups", len(self.plugins))
        for plugin in self.plugins.values():
            self.tasks.append(asyncio.create_task(self._health_loop(plugin), name=f"plugin-health-{plugin.metadata.id}"))
            if isinstance(plugin, DatasourcePlugin):
                self.tasks.append(asyncio.create_task(self._datasource_loop(plugin), name=f"plugin-datasource-{plugin.metadata.id}"))
            if isinstance(plugin, PeriodicPlugin):
                self.tasks.append(asyncio.create_task(self._periodic_loop(plugin), name=f"plugin-periodic-{plugin.metadata.id}"))

    async def shutdown(self) -> None:
        if not self.tasks:
            return
        logger.info("Stopping %d plugin tasks", len(self.tasks))
        for task in self.tasks:
            task.cancel()
        done, pending = await asyncio.wait(self.tasks, timeout=5)
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Plugin task failed during shutdown")
        for task in pending:
            task.cancel()
        self.tasks.clear()

    async def _health_loop(self, plugin: Plugin) -> None:
        # Health checks are separate from datasource/periodic work on purpose:
        # diagnostics should still update when a collector is idle or disabled.
        while True:
            db = SessionLocal()
            try:
                ctx = self.context(db, plugin)
                enabled = ctx.get("enabled", "false").lower() == "true"
                if not enabled:
                    self._update_diagnostic(db, plugin.metadata.id, "disabled", "Plugin is disabled and not running.")
                else:
                    result = await plugin.health(ctx)
                    logger.debug("Plugin %s health: %s", plugin.metadata.id, result)
                    self._update_diagnostic(
                        db,
                        plugin.metadata.id,
                        result.get("status", "healthy"),
                        result.get("message") or result.get("error"),
                    )
                db.commit()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                db.close()
                raise
            except Exception as exc:
                logger.exception("Plugin %s health check failed", plugin.metadata.id)
                self._update_diagnostic(db, plugin.metadata.id, "error", str(exc))
                db.commit()
                await asyncio.sleep(60)
            finally:
                db.close()

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
                    found = 0
                    stored_count = 0
                    duplicate_count = 0
                    last_event_at = None
                    for event in events:
                        stored = ctx.emit_event(**event)
                        found += 1
                        if getattr(stored, "_opensecdash_created", False):
                            stored_count += 1
                        else:
                            duplicate_count += 1
                        last_event_at = stored.event_time
                    if found:
                        logger.debug(
                            "Datasource plugin %s processed events found=%d stored=%d duplicates=%d",
                            plugin.metadata.id,
                            found,
                            stored_count,
                            duplicate_count,
                        )
                    db.commit()
                    self._update_datasource(db, plugin.metadata.id, True, "healthy", None, stored_count, last_event_at)
                    db.commit()
                await asyncio.sleep(max(interval, 1))
            except asyncio.CancelledError:
                db.close()
                raise
            except Exception as exc:
                logger.exception("Datasource plugin %s failed", plugin.metadata.id)
                db.rollback()
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
                sleep_for = 60
                publish_interval = ctx.get("publish_interval", "")
                poll_interval = ctx.get("poll_interval", "")
                if publish_interval.isdigit() and int(publish_interval) > 0:
                    sleep_for = max(int(publish_interval), 1)
                elif poll_interval.isdigit() and int(poll_interval) > 0:
                    sleep_for = max(int(poll_interval), 1)
                await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                db.close()
                raise
            except Exception as exc:
                logger.exception("Periodic plugin %s failed", plugin.metadata.id)
                db.rollback()
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

    async def export_asset_update(self, db: Session, asset: Any, manual: bool = False) -> None:
        # Cross-plugin calls must attribute failures to the callee. For example,
        # Apps Inventory can trigger MQTT, but auth errors belong to MQTT's
        # diagnostic row, not the Apps Inventory plugin.
        for plugin in self.plugins.values():
            if isinstance(plugin, ExportPlugin):
                ctx = self.context(db, plugin, manual_export=manual)
                enabled = ctx.get("enabled", "false").lower() == "true"
                if not enabled:
                    continue
                try:
                    await plugin.export_asset(ctx, asset)
                except Exception as exc:
                    logger.exception("Export plugin %s failed while exporting asset", plugin.metadata.id)
                    db.rollback()
                    self._update_diagnostic(db, plugin.metadata.id, "error", str(exc))
                    db.commit()


_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        root = Path(__file__).resolve().parents[3]
        _manager = PluginManager(root / "plugins")
        _manager.discover()
    return _manager
