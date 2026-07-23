from __future__ import annotations

import asyncio
import logging
import json
import time
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.plugins.web import PluginWebRegistration

from app.core import plugin_registry
from app.core.i18n import clear_extra_locales, register_extra_locales
from app.core.template_context import get_setting_value
from app.database.session import SessionLocal
from app.models.assets import Asset
from app.models.core import Datasource, Diagnostic, InsightRule as InsightRuleModel, PluginRecord
from app.models.settings import Setting
from app.plugins.base import ActionDefinition, CURRENT_PLUGIN_API_VERSION, ActionPlugin, DatasourcePlugin, ExportPlugin, PeriodicPlugin, Plugin, PluginContext, PluginSetting
from app.plugins.loader import env_disable_var, import_plugin_module, is_plugin_env_disabled
from app.core.time import utc_now
from app.services.events import cleanup_events_by_retention, clear_duplicate_rules, compact_completed_daily_rollups, register_duplicate_rules, store_event
from app.services.geoip import enrich_pending_events
from app.services.insight_rules import import_ruleset, invalidate_active_rules_cache, refresh_insight_rules
from app.services.asset_updates import refresh_asset_updates
from app.services.self_update import run_self_update_check
from app.services.notifications import dispatch_pending_notifications


logger = logging.getLogger(__name__)

BACKLOG_CATCHUP_DELAY_SECONDS = 0.2
GEOIP_BACKFILL_BATCH_SIZE = 50
EVENTS_COMMIT_EVERY = 200

_UNSET: Any = object()


def run_with_session(work: Callable[..., Any], *args: Any) -> Any:
    """Run one threaded tick with a session owned by the worker thread.

    Background loops hand their database work to ``asyncio.to_thread``, and
    cancelling a loop cannot stop a thread that is already running. A session
    created on the event loop and closed in the loop's ``finally`` is therefore
    closed while the worker may still be inside ``commit()``, which SQLAlchemy
    rejects with ``IllegalStateChangeError`` during shutdown. Creating, using
    and closing the session on the same worker thread removes that race.
    """
    db = SessionLocal()
    try:
        return work(db, *args)
    finally:
        db.close()


class PluginManager:
    """Discovers and orchestrates external plugins.

    External plugins live outside ``app/`` by design. The manager owns lifecycle,
    settings lookup, diagnostics, and cross-plugin calls so plugins can stay
    small and ADR-compliant.

    Integration-specific domain services live in their plugin packages. Core
    code must not import from ``plugins/``; cross-plugin behavior goes through
    this manager, registries, and plugin hooks.
    """

    def __init__(self, plugin_dir: Path) -> None:
        self.plugin_dir = plugin_dir
        self.plugins: dict[str, Plugin] = {}
        self.tasks: list[asyncio.Task] = []

    def discover(self) -> None:
        # Discovery is file-system based to keep packaging simple for community
        # plugins: each plugin is a package directory (``__init__.py``) with a
        # ``plugin.py`` exposing ``Plugin``. Loaded via the osd_plugins
        # namespace (see app.plugins.loader) so plugins can ship their own
        # submodules and import them relatively.
        self.plugins.clear()
        clear_extra_locales()
        clear_duplicate_rules()
        plugin_registry.register_plugins(())
        if not self.plugin_dir.exists():
            logger.warning("Plugin directory does not exist: %s", self.plugin_dir)
            return
        for plugin_py in sorted(self.plugin_dir.glob("*/plugin.py")):
            plugin_dir = plugin_py.parent
            if not (plugin_dir / "__init__.py").exists():
                logger.warning(
                    "Skipping plugin directory without __init__.py (packages are required since plugin API 2): %s",
                    plugin_dir,
                )
                continue
            # Env-disabled plugins are not even imported, so they are absent
            # everywhere downstream (settings, seeding, loops, nav, feature
            # flags). Checked by directory name first to skip the import.
            if is_plugin_env_disabled(plugin_dir.name):
                logger.info("Plugin %s is disabled via %s and will not be loaded", plugin_dir.name, env_disable_var(plugin_dir.name))
                continue
            # One broken plugin must not take down discovery of the others.
            try:
                module = import_plugin_module(plugin_dir, "plugin")
            except Exception:
                logger.exception("Failed to load plugin from %s; skipping it", plugin_dir)
                continue
            plugin_class = getattr(module, "Plugin", None)
            if plugin_class is None:
                continue
            plugin: Plugin = plugin_class()
            if plugin.metadata.api_version != CURRENT_PLUGIN_API_VERSION:
                logger.warning(
                    "Plugin %s declares API version %s, current API version is %s; loading anyway for compatibility",
                    plugin.metadata.id,
                    plugin.metadata.api_version,
                    CURRENT_PLUGIN_API_VERSION,
                )
            # Second check: the id can differ from the directory name (e.g. dir
            # "mqtt" but id "mqtt-hass"), so both spellings can disable it.
            if is_plugin_env_disabled(plugin.metadata.id):
                logger.info("Plugin %s is disabled via %s and will not be loaded", plugin.metadata.id, env_disable_var(plugin.metadata.id))
                continue
            self.plugins[plugin.metadata.id] = plugin
            # Plugin translations become globally resolvable via t()/translate()
            # (core strings still win on key collision, see app.core.i18n).
            register_extra_locales(plugin.locales)
            # Plugin-provided event dedupe rules (e.g. CrowdSec ban correlation).
            register_duplicate_rules(plugin.metadata.id, plugin.duplicate_rules())
            logger.debug("Discovered plugin %s from %s", plugin.metadata.id, plugin_py)

        # Publish the discovered set so dependency-free core code (feature flags,
        # nav, websocket gating) can answer "which plugins / capabilities exist"
        # without importing the manager.
        plugin_registry.register_plugins(
            plugin_registry.RegisteredPlugin(
                id=p.metadata.id,
                name=p.metadata.name,
                capabilities=tuple(p.metadata.capabilities),
                nav_items=self._nav_items_for(p),
            )
            for p in self.plugins.values()
        )

    @staticmethod
    def _nav_items_for(plugin: Plugin) -> tuple[plugin_registry.NavItem, ...]:
        registration = plugin.web()
        if registration is None:
            return ()
        return tuple((item.label_key, item.href, item.active_prefix, item.order) for item in registration.nav_items)

    def web_registrations(self) -> list[tuple[str, "PluginWebRegistration"]]:
        result: list[tuple[str, PluginWebRegistration]] = []
        for plugin in self.plugins.values():
            registration = plugin.web()
            if registration is not None:
                result.append((plugin.metadata.id, registration))
        return result

    def default_views(self) -> list[dict[str, Any]]:
        """Collect read-only saved-view descriptors from loaded plugins."""
        result = []
        for plugin_id, plugin in self.plugins.items():
            try:
                result.extend({**view, "plugin_id": plugin_id} for view in plugin.default_views() if isinstance(view, dict))
            except Exception:
                logger.exception("Failed to load default views from plugin %s", plugin_id)
        return result

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
                diagnostic = Diagnostic(plugin=meta.id, component="plugin", status="healthy")
                db.add(diagnostic)

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

            try:
                ruleset = plugin.insight_rules()
                if ruleset is not None:
                    import_ruleset(db, ruleset, source=f"plugin:{meta.id}")
            except Exception as exc:
                diagnostic.status = "warning"
                diagnostic.last_error = f"Insight rules import failed: {exc}"
                diagnostic.last_run = utc_now().replace(tzinfo=None)
                logger.warning("Failed to import insight rules for plugin %s: %s", meta.id, exc)

        active_plugin_sources = {f"plugin:{plugin_id}" for plugin_id in self.plugins}
        stale_rules = (
            db.query(InsightRuleModel)
            .filter(InsightRuleModel.source.startswith("plugin:"), InsightRuleModel.source.notin_(active_plugin_sources))
            .all()
        )
        for rule in stale_rules:
            rule.is_active = False
        if stale_rules:
            invalidate_active_rules_cache(db)
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
        self.tasks.append(asyncio.create_task(self._asset_update_loop(), name="core-asset-update-checks"))
        self.tasks.append(asyncio.create_task(self._insight_rules_loop(), name="core-insight-rules"))
        self.tasks.append(asyncio.create_task(self._rollup_compaction_loop(), name="core-rollup-compaction"))
        self.tasks.append(asyncio.create_task(self._retention_cleanup_loop(), name="core-retention-cleanup"))
        self.tasks.append(asyncio.create_task(self._geoip_backfill_loop(), name="core-geoip-backfill"))
        self.tasks.append(asyncio.create_task(self._self_update_check_loop(), name="core-self-update-check"))
        self.tasks.append(asyncio.create_task(self._notification_dispatch_loop(), name="core-notification-dispatch"))
        for plugin in self.plugins.values():
            self.tasks.append(asyncio.create_task(self._health_loop(plugin), name=f"plugin-health-{plugin.metadata.id}"))
            if isinstance(plugin, DatasourcePlugin):
                self.tasks.append(asyncio.create_task(self._datasource_loop(plugin), name=f"plugin-datasource-{plugin.metadata.id}"))
            if isinstance(plugin, PeriodicPlugin):
                self.tasks.append(asyncio.create_task(self._periodic_loop(plugin), name=f"plugin-periodic-{plugin.metadata.id}"))

    async def _asset_update_loop(self) -> None:
        # Runs in a thread: the update check makes one GitHub API request per
        # unique repo, which on a slow connection would otherwise freeze the
        # event loop (and with it every page view) for the whole check.
        last_run = 0.0
        while True:
            try:
                last_run = await asyncio.to_thread(run_with_session, self._run_asset_update_tick, last_run)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Asset update check failed")
                await asyncio.sleep(60)

    def _run_asset_update_tick(self, db: Session, last_run: float) -> float:
        """Runs one asset-update check synchronously. Called via ``asyncio.to_thread``."""
        asset_sources_enabled = any(
            get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"
            for plugin_id in plugin_registry.ids_with_capability("asset_source")
        )
        interval = self._setting_interval(get_setting_value(db, "asset_updates.github_interval", "21600"))
        if not asset_sources_enabled:
            self._update_diagnostic(db, "asset_updates", "disabled", "No asset source plugin is enabled.")
            db.commit()
        elif interval <= 0:
            self._update_diagnostic(db, "asset_updates", "disabled", "Automatic asset update checks are disabled.")
            db.commit()
        else:
            now = time.monotonic()
            if last_run == 0 or now - last_run >= interval:
                result = refresh_asset_updates(db)
                logger.debug("Asset update checks completed: %s", result)
                failed_assets = result.get("failed_assets") or []
                failed_reasons = result.get("failed_reasons") or []
                reasons_text = "; ".join(failed_reasons) or "unknown error"
                rate_limited = "rate limit" in reasons_text.lower()
                if failed_assets and result["failed"] == result["checked"] and rate_limited:
                    failed_suffix = f"; all checks failed: {reasons_text}"
                elif failed_assets and result["failed"] == result["checked"]:
                    failed_suffix = f"; all checks failed: {reasons_text}; affected assets: {', '.join(failed_assets)}"
                else:
                    failed_suffix = f"; failed assets: {', '.join(failed_assets)}" if failed_assets else ""
                status = "warning" if result["failed"] else "healthy"
                self._update_diagnostic(
                    db,
                    "asset_updates",
                    status,
                    f"Last check: checked={result['checked']}, updated={result['updated']}, failed={result['failed']}{failed_suffix}",
                )
                db.commit()
                last_run = now
                asyncio.run(self._export_publishable_assets(db))
        return last_run

    @staticmethod
    def _setting_interval(value: str) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    async def _export_publishable_assets(self, db: Session) -> None:
        publishable_assets = (
            db.query(Asset)
            .filter(
                Asset.mqtt_publish_enabled == True,
                Asset.version.isnot(None),
                Asset.latest_version.isnot(None),
                Asset.release_url.isnot(None),
            )
            .all()
        )
        for asset in publishable_assets:
            await self.export_asset_update(db, asset)

    async def _insight_rules_loop(self) -> None:
        while True:
            try:
                result = await asyncio.to_thread(run_with_session, refresh_insight_rules)
                logger.debug("Insights engine rules refresh result: %s", result)
                await asyncio.sleep(24 * 60 * 60)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Insights engine rules refresh failed")
                await asyncio.sleep(60 * 60)

    async def _rollup_compaction_loop(self) -> None:
        while True:
            try:
                compacted = await asyncio.to_thread(run_with_session, self._run_rollup_compaction)
                if compacted:
                    logger.info("Compacted %d completed rollup month(s)", compacted)
                await asyncio.sleep(60 * 60)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Rollup compaction failed")
                await asyncio.sleep(60 * 60)

    @staticmethod
    def _run_rollup_compaction(db: Session) -> int:
        """Runs one compaction pass synchronously. Called via ``asyncio.to_thread``."""
        compacted = compact_completed_daily_rollups(db)
        db.commit()
        return compacted

    async def _retention_cleanup_loop(self) -> None:
        # Runs in a thread: on a busy instance the retention DELETE can touch
        # tens of thousands of rows in one transaction, which must not stall
        # the event loop while SQLite works through it.
        while True:
            try:
                deleted = await asyncio.to_thread(run_with_session, self._run_retention_cleanup)
                if deleted:
                    logger.info("Retention cleanup removed %d raw event(s)", deleted)
                await asyncio.sleep(60 * 60)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Retention cleanup failed")
                await asyncio.sleep(60 * 60)

    def _run_retention_cleanup(self, db: Session) -> int:
        """Runs one retention cleanup synchronously. Called via ``asyncio.to_thread``."""
        retention_days = self._setting_interval(get_setting_value(db, "retention_days", "30"))
        deleted = cleanup_events_by_retention(db, retention_days)
        db.commit()
        return deleted

    async def _geoip_backfill_loop(self) -> None:
        while True:
            try:
                # Runs in a thread: a slow/unreachable GeoIP provider can mean
                # a real network wait per uncached IP, which must not block
                # the event loop while it's happening.
                processed = await asyncio.to_thread(run_with_session, enrich_pending_events, GEOIP_BACKFILL_BATCH_SIZE)
                # Keep draining quickly while a backlog exists (e.g. right
                # after a large log import), back off once caught up.
                await asyncio.sleep(1 if processed >= GEOIP_BACKFILL_BATCH_SIZE else 15)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GeoIP backfill failed")
                await asyncio.sleep(30)

    async def _self_update_check_loop(self) -> None:
        # Runs in a thread: one GitHub API request, which must not block the
        # event loop on a slow connection. Every 6 hours keeps well within
        # GitHub's unauthenticated rate limits and is timely enough for a
        # "new version available" footer hint.
        while True:
            try:
                await asyncio.to_thread(run_with_session, run_self_update_check)
                await asyncio.sleep(6 * 60 * 60)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("OpenSecDash update check failed")
                await asyncio.sleep(60 * 60)

    async def _notification_dispatch_loop(self) -> None:
        while True:
            try:
                sent = await asyncio.to_thread(run_with_session, dispatch_pending_notifications)
                if sent:
                    logger.info("Dispatched %d notification(s)", sent)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Notification dispatch failed")
                await asyncio.sleep(30)

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
        # Runs in a thread: health checks block on real I/O (LAPI and Proxmox
        # API requests, MQTT socket connects up to 5s) and
        # must not freeze the event loop for that long.
        while True:
            try:
                await asyncio.to_thread(run_with_session, self._run_health_tick, plugin)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Plugin %s health check failed", plugin.metadata.id)
                await asyncio.to_thread(run_with_session, self._record_loop_error, plugin.metadata.id, str(exc))
                await asyncio.sleep(60)

    def _record_loop_error(self, db: Session, plugin_id: str, message: str, update_datasource: bool = False) -> None:
        """Persists a loop failure synchronously. Called via ``asyncio.to_thread``.

        Error commits must go through a thread like every other write: they
        wait on the app-wide write lock, and the event loop waiting on a lock
        held by a busy import thread would freeze request dispatching for
        every client - the loop must never block on the write lock itself.
        """
        db.rollback()
        self._update_diagnostic(db, plugin_id, "error", message)
        if update_datasource:
            self._update_datasource(db, plugin_id, True, "error", message, 0)
        db.commit()

    def refresh_health_diagnostics(self, db: Session) -> None:
        """Refresh plugin health rows after settings changed.

        This keeps Diagnostics in sync immediately when a plugin is toggled or
        a connection mode/path changes, instead of showing a stale background
        health result until the next scheduled health loop.
        """
        for plugin in self.plugins.values():
            try:
                self._run_health_tick(db, plugin, commit=False)
                self._refresh_extra_diagnostics(db, plugin)
            except Exception as exc:
                logger.exception("Plugin %s health check failed after settings save", plugin.metadata.id)
                db.rollback()
                self._update_diagnostic(db, plugin.metadata.id, "error", str(exc))
        db.commit()

    def _refresh_extra_diagnostics(self, db: Session, plugin: Plugin) -> None:
        """Let an enabled plugin refresh its non-health diagnostic components.

        Skipped while the plugin is disabled: its background loops aren't
        running, so probing its backend (LAPI, cscli, ...) here would report a
        state that doesn't correspond to anything actually running.
        """
        ctx = self.context(db, plugin)
        if ctx.get("enabled", "false").lower() != "true":
            return
        plugin.refresh_diagnostics(db)

    def _run_health_tick(self, db: Session, plugin: Plugin, *, commit: bool = True) -> None:
        """Runs one health check synchronously. Called via ``asyncio.to_thread``."""
        ctx = self.context(db, plugin)
        enabled = ctx.get("enabled", "false").lower() == "true"
        if not enabled:
            self._update_diagnostic(db, plugin.metadata.id, "disabled", "Plugin is disabled and not running.")
        else:
            result = asyncio.run(plugin.health(ctx))
            logger.debug("Plugin %s health: %s", plugin.metadata.id, result)
            self._update_diagnostic(
                db,
                plugin.metadata.id,
                result.get("status", "healthy"),
                result.get("message") or result.get("error"),
            )
        if commit:
            db.commit()

    async def _datasource_loop(self, plugin: DatasourcePlugin) -> None:
        while True:
            try:
                # The whole tick (file I/O, event storage, commits) runs in a
                # worker thread. A plugin's first run against a large existing
                # log can otherwise take long enough to freeze the entire app
                # for every visitor, since it shares one event loop with the
                # web server. Batches are capped (see tail_text_file callers)
                # so each threaded tick still finishes quickly.
                interval, backlog_pending = await asyncio.to_thread(run_with_session, self._run_datasource_tick, plugin)
                await asyncio.sleep(self._next_datasource_delay(interval, backlog_pending))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Datasource plugin %s failed", plugin.metadata.id)
                await asyncio.to_thread(run_with_session, self._record_loop_error, plugin.metadata.id, str(exc), True)
                await asyncio.sleep(10)

    def _run_datasource_tick(self, db: Session, plugin: DatasourcePlugin) -> tuple[int, bool]:
        """Runs one full datasource tick synchronously. Called via ``asyncio.to_thread``."""
        ctx = self.context(db, plugin)
        interval = int(ctx.get("poll_interval", "5") or "5")
        enabled = ctx.get("enabled", "false").lower() == "true"

        self._update_datasource(
            db,
            plugin.metadata.id,
            enabled,
            "running" if enabled else "disabled",
            None,
            0,
            backlog_pending=False if not enabled else _UNSET,
            backlog_progress_percent=None if not enabled else _UNSET,
        )
        if not enabled:
            # Without this commit, disabling a plugin never persists its
            # "disabled" status/enabled flag - the row (and any stale
            # backlog_pending flag) would silently keep showing whatever it
            # last was, since the session closes uncommitted below.
            db.commit()
            return interval, False

        events = asyncio.run(plugin.collect(ctx))
        found = 0
        stored_count = 0
        duplicate_count = 0
        last_event_at = None
        for event in events:
            found += 1
            try:
                # Savepoint per event: one malformed event must not abort the
                # whole batch (the plugin's file offset has already advanced
                # past these lines, so anything after an aborting event would
                # be lost for good) - and rolling back only to the savepoint
                # keeps the batch's already-staged good events intact.
                with db.begin_nested():
                    stored = ctx.emit_event(**event)
            except Exception:
                logger.exception("Datasource plugin %s failed to store one event; skipping it", plugin.metadata.id)
                continue
            if getattr(stored, "_opensecdash_created", False):
                stored_count += 1
            else:
                duplicate_count += 1
            last_event_at = stored.event_time
            # Commit periodically rather than once for the whole (up to
            # MAX_LINES_PER_TICK) batch, so a single tick doesn't hold the
            # SQLite write lock for its entire duration - other writers get
            # a chance to interleave, and a mid-batch crash/restart loses
            # at most one partial chunk instead of the whole tick.
            if found % EVENTS_COMMIT_EVERY == 0:
                db.commit()
                # Lock releases don't hand off fairly: without this pause the
                # import thread re-acquires the write lock microseconds after
                # releasing it and starves every other writer (settings
                # saves, error diagnostics) for the whole backlog import.
                # Bulk work queues behind interactive writes, not vice versa.
                time.sleep(0.02)
        if found:
            logger.debug(
                "Datasource plugin %s processed events found=%d stored=%d duplicates=%d",
                plugin.metadata.id,
                found,
                stored_count,
                duplicate_count,
            )
        db.commit()
        self._update_datasource(
            db,
            plugin.metadata.id,
            True,
            "healthy",
            None,
            stored_count,
            last_event_at,
            backlog_pending=ctx.backlog_pending,
            backlog_progress_percent=ctx.backlog_progress_percent,
        )
        db.commit()
        return interval, ctx.backlog_pending

    @staticmethod
    def _next_datasource_delay(interval: int, backlog_pending: bool) -> float:
        if backlog_pending:
            # Drain a large backlog across many quick batches instead of
            # waiting a full poll_interval between each one, while the brief
            # sleep still gives the event loop a chance to serve requests.
            return BACKLOG_CATCHUP_DELAY_SECONDS
        return max(interval, 1)

    async def _periodic_loop(self, plugin: PeriodicPlugin) -> None:
        # Runs in a thread: periodic ticks block on real I/O (LAPI requests for
        # decision sync, Proxmox API requests, MQTT publishes,
        # JSON source fetches) and must not freeze the event loop for that
        # long - a slow tick used to make every page view hang with it.
        while True:
            try:
                sleep_for = await asyncio.to_thread(run_with_session, self._run_periodic_tick, plugin)
                await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Periodic plugin %s failed", plugin.metadata.id)
                await asyncio.to_thread(run_with_session, self._record_loop_error, plugin.metadata.id, str(exc))
                await asyncio.sleep(60)

    def _run_periodic_tick(self, db: Session, plugin: PeriodicPlugin) -> int:
        """Runs one periodic tick synchronously. Called via ``asyncio.to_thread``."""
        ctx = self.context(db, plugin)
        enabled = ctx.get("enabled", "false").lower() == "true"
        if enabled:
            asyncio.run(plugin.tick(ctx))
            db.commit()
        sleep_for = 60
        publish_interval = ctx.get("publish_interval", "")
        poll_interval = ctx.get("poll_interval", "")
        if publish_interval.isdigit() and int(publish_interval) > 0:
            sleep_for = max(int(publish_interval), 1)
        elif poll_interval.isdigit() and int(poll_interval) > 0:
            sleep_for = max(int(poll_interval), 1)
        return sleep_for

    def _update_datasource(
        self,
        db: Session,
        plugin_id: str,
        enabled: bool,
        status: str,
        error: str | None,
        processed: int,
        last_event_at: Any | None = None,
        backlog_pending: bool = _UNSET,
        backlog_progress_percent: int | None = _UNSET,
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
        # Callers that only refresh enabled/status (not the result of an
        # actual collect() run) omit these, leaving the last known value in
        # place instead of flickering it back to "not pending" every tick.
        if backlog_pending is not _UNSET:
            datasource.backlog_pending = backlog_pending
        if backlog_progress_percent is not _UNSET:
            datasource.backlog_progress_percent = backlog_progress_percent

    def _update_diagnostic(self, db: Session, plugin_id: str, status: str, error: str | None) -> None:
        diagnostic = db.query(Diagnostic).filter(Diagnostic.plugin == plugin_id, Diagnostic.component == "plugin").first()
        if diagnostic is None:
            diagnostic = Diagnostic(plugin=plugin_id, component="plugin")
            db.add(diagnostic)
        previous_status = diagnostic.status
        diagnostic.status = status
        diagnostic.last_error = error
        diagnostic.last_run = utc_now().replace(tzinfo=None)
        if previous_status != "error" and status == "error":
            store_event(
                db,
                source="System",
                source_id="diagnostics",
                plugin="core",
                plugin_id="core",
                event_type="system.plugin_error",
                severity="error",
                data_json={"plugin": plugin_id, "message": error},
            )

    def action_plugin_for(self, action_type: str) -> ActionPlugin | None:
        for plugin in self.plugins.values():
            if isinstance(plugin, ActionPlugin) and action_type in plugin.action_types:
                return plugin
        return None

    def critical_action_types(self) -> frozenset[str]:
        result: frozenset[str] = frozenset()
        for plugin in self.plugins.values():
            if isinstance(plugin, ActionPlugin):
                result |= plugin.critical_action_types
        return result

    def action_definitions(self) -> list[tuple[str, ActionDefinition]]:
        result: list[tuple[str, ActionDefinition]] = []
        seen_types: set[str] = set()
        for plugin in self.plugins.values():
            if not isinstance(plugin, ActionPlugin):
                continue
            for definition in plugin.action_definitions:
                definition_types = {definition.action_type, *definition.aliases}
                collisions = definition_types & seen_types
                if collisions:
                    logger.warning(
                        "Ignoring colliding action definition from plugin %s: %s",
                        plugin.metadata.id,
                        sorted(collisions),
                    )
                    continue
                seen_types.update(definition_types)
                result.append((plugin.metadata.id, definition))
        return result

    def available_actions(self, db: Session, target_type: str, target: str) -> list[tuple[str, ActionDefinition]]:
        dry_run = get_setting_value(db, "action_dry_run", "true").lower() == "true"
        result: list[tuple[str, ActionDefinition]] = []
        for plugin_id, definition in self.action_definitions():
            plugin = self.plugins.get(plugin_id)
            if not isinstance(plugin, ActionPlugin) or target_type not in definition.target_types:
                continue
            if plugin.action_available(db, definition.action_type, target, dry_run):
                result.append((plugin_id, definition))
        return result

    def plugin_id_for_action(self, action_type: str) -> str:
        plugin = self.action_plugin_for(action_type)
        return plugin.metadata.id if plugin else "core"

    async def execute_action(self, db: Session, action_type: str, target: str, parameters: dict[str, Any]) -> dict[str, Any] | None:
        plugin = self.action_plugin_for(action_type)
        if plugin is None:
            return None
        ctx = self.context(db, plugin)
        return await plugin.execute(ctx, action_type, target, parameters)

    async def export_asset_update(self, db: Session, asset: Any, manual: bool = False) -> None:
        # Cross-plugin calls must attribute failures to the callee. For example,
        # JSON Assets can trigger MQTT, but auth errors belong to MQTT's
        # diagnostic row, not the JSON Assets plugin.
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
