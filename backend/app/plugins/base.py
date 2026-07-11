from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.orm import Session

from app.models.events import Event
from app.services.events import DuplicateRule, store_event

if TYPE_CHECKING:
    # Type-only import keeps base.py free of FastAPI/Jinja at runtime while the
    # web() hook can still annotate its return type. See app.plugins.web.
    from app.plugins.web import PluginWebRegistration
    from app.web.dashboard import DashboardWidget

# Keep this module intentionally dependency-light: external plugins import it as
# their public API surface. Changes here should be backwards compatible or paired
# with an ADR/plugin API version bump.
#
# Plugins can register web surfaces through ``Plugin.web()`` (routers,
# namespaced template directories, nav items, IP panels) while keeping FastAPI
# and Jinja imports out of this base module at runtime.
CURRENT_PLUGIN_API_VERSION = "2"

PluginCapability = Literal["datasource", "asset_source", "enrichment", "action", "export", "page", "widget", "insight"]
SettingType = Literal["text", "password", "number", "boolean", "select", "file", "url"]


@dataclass(frozen=True)
class TailResult:
    """Result of an incremental, resumable text file read.

    ``more_available`` is true when ``max_lines`` was reached before EOF, so
    the caller knows there is more backlog to drain on a following call
    instead of having reached the end of the file.
    """

    lines: list[str]
    offset: int
    inode: int
    file_size: int
    more_available: bool


def tail_text_file(
    path: Path,
    offset: int = 0,
    inode: int | None = None,
    max_lines: int | None = None,
    last_size: int | None = None,
) -> TailResult:
    """Read new lines from ``path`` starting at ``offset``, capped at ``max_lines``.

    Handles log rotation/truncation by resetting to the start of the file
    when any of these indicate it: the inode changed (rename-then-create
    rotation), the file is now smaller than ``offset`` (truncation caught
    before catching up to it), or it's smaller than ``last_size`` - the raw
    size observed on the previous call, passed back in by the caller.

    That last check matters for copytruncate-style rotation (same inode,
    truncated to empty and appended to in place, common when the writer
    can't be told to reopen its file handle): comparing only against
    ``offset`` misses the rotation if enough new data lands between calls to
    grow the file past the old (already behind, e.g. capped) offset again
    before this runs. ``last_size`` reflects the file as it actually was at
    the last check, not how far reading has progressed, so it only misses a
    rotation if a full rewrite happens within a single call-to-call gap.

    Uses ``readline()`` rather than iterating the file object directly,
    because ``tell()`` is only reliable for resuming a read when it isn't
    interleaved with the iterator protocol's internal read-ahead buffering.
    """
    stat = path.stat()
    rotated = inode is not None and stat.st_ino != inode
    truncated = stat.st_size < offset or (last_size is not None and stat.st_size < last_size)
    if rotated or truncated:
        offset = 0

    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        handle.seek(offset)
        while max_lines is None or len(lines) < max_lines:
            line = handle.readline()
            if not line:
                break
            lines.append(line)
        new_offset = handle.tell()
        more_available = max_lines is not None and len(lines) >= max_lines and handle.readline() != ""

    return TailResult(lines=lines, offset=new_offset, inode=stat.st_ino, file_size=stat.st_size, more_available=more_available)


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
    # "2" means package layout with mandatory __init__.py, relative plugin
    # imports, optional web()/ip_page_context()/duplicate_rules() hooks, the
    # action hook family, and the asset_source capability.
    api_version: str = CURRENT_PLUGIN_API_VERSION
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
        self.backlog_pending = False
        self.backlog_progress_percent: int | None = None

    def get(self, key: str, default: str = "") -> str:
        return self.settings.get(key, default)

    def emit_event(self, **values: Any) -> Event:
        return store_event(self.db, **values)

    def report_backlog(self, more_available: bool, progress_percent: int | None = None) -> None:
        """Let a datasource plugin signal it hasn't drained its full backlog yet.

        Called from ``collect()`` after a capped/incremental read. The manager
        reads this back after the call to keep retrying sooner and to surface
        catch-up progress in the UI, without changing ``collect()``'s return type.
        """
        self.backlog_pending = more_available
        self.backlog_progress_percent = progress_percent if more_available else None

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

    def web(self) -> "PluginWebRegistration | None":  # noqa: F821 - see app.plugins.web
        """Optional web surface (router, templates, nav). See app.plugins.web."""
        return None

    def duplicate_rules(self) -> tuple[DuplicateRule, ...]:
        """Plugin-provided event dedupe rules (see app.services.events)."""
        return ()

    def insight_rules(self) -> dict[str, Any] | None:
        """Optional declarative insight ruleset (same JSON schema as
        default-rules.json). Validated by the core; data only, never code.
        """
        return None

    def ip_page_context(self, db: Session, ip: str) -> dict[str, Any]:
        """Extra template context for the IP explorer page (side-effect-free).

        Returned keys are merged unprefixed into the core template context.
        Plugin authors should prefix keys with their plugin id (for example
        ``crowdsec_decisions``) to avoid collisions with core context values or
        other trusted plugins.
        """
        return {}

    def ip_page_count_widgets(self, db: Session, ip: str) -> list[dict[str, Any]]:
        """Count cards this plugin contributes to the IP explorer (side-effect-free).

        Each dict has ``key`` (i18n suffix under "ip.count."), ``value`` and
        ``href``. Only shown while the plugin is enabled - the plugin decides.
        """
        return []

    def dashboard_widgets(self, db: Session) -> list["DashboardWidget"]:  # noqa: F821
        """Counter/table/feed/trend widgets this plugin contributes to the dashboard.

        Descriptors only (validated and rendered by core), never HTML. Only return
        widgets while the plugin is enabled - the plugin decides, mirroring
        ``ip_page_count_widgets``. See app.web.dashboard.DashboardWidget.
        """
        return []


class DatasourcePlugin(Plugin):
    async def collect(self, context: PluginContext) -> Iterable[dict[str, Any]]:
        return []


class PeriodicPlugin(Plugin):
    async def tick(self, context: PluginContext) -> None:
        return None


@dataclass(frozen=True)
class ActionParameter:
    """Declarative form parameter of an action (rendered by the core UI)."""

    name: str
    kind: str = "select"
    options: tuple[str, ...] = ()
    default: str | None = None
    label_key: str | None = None


@dataclass(frozen=True)
class ActionDefinition:
    """ADR-016 "Action Definition" / ADR-029 "Action registration"."""

    action_type: str
    label_key: str
    target_types: frozenset[str]
    aliases: frozenset[str] = frozenset()
    description_key: str | None = None
    critical: bool = False
    permission: str = ""
    parameters: tuple[ActionParameter, ...] = ()


class ActionPlugin(Plugin):
    # Action definitions are the source for routing, plugin_id attribution and
    # (via critical_action_types) the confirmation/IP-validation gate.
    action_definitions: tuple[ActionDefinition, ...] = ()

    @property
    def action_types(self) -> frozenset[str]:
        return frozenset(
            action_type
            for definition in self.action_definitions
            for action_type in (definition.action_type, *definition.aliases)
        )

    @property
    def critical_action_types(self) -> frozenset[str]:
        return frozenset(
            action_type
            for definition in self.action_definitions
            if definition.critical
            for action_type in (definition.action_type, *definition.aliases)
        )

    def action_available(self, db: Session, action_type: str, target: str, dry_run: bool) -> bool:
        """Whether the action should be offered for this target right now."""
        return True

    async def execute(
        self,
        context: PluginContext,
        action_type: str,
        target: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    def validate_action(
        self, db: Session, action_type: str, target: str, parameters: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        """Validate/normalize parameters before the Action row is created.

        Raise ValueError to reject (message is shown to the user / stored on
        the failed action). Returns the (possibly updated) parameters.
        """
        return parameters

    def prepare_parameters(self, db: Session, action: Any) -> dict[str, Any] | None:
        """Called after the Action row got its id (db.flush), before execution.

        Return a NEW parameters dict to replace action.parameters, or None to
        keep them. Runs in dry-run too - must be side-effect-free.
        """
        return None

    def success_event_type(self, action_type: str) -> str | None:
        """Event type stored for a completed action (None -> "action.executed")."""
        return None

    def action_event_data(self, action: Any) -> dict[str, Any]:
        """Extra data_json fields for the action's event. Runs in dry-run too."""
        return {}

    def after_execute(self, db: Session, action: Any) -> None:
        """Called after successful non-dry-run execution (e.g. state re-sync)."""
        return None


class ExportPlugin(Plugin):
    async def export_event(self, context: PluginContext, event: Event) -> None:
        return None

    async def export_asset(self, context: PluginContext, asset: Any) -> None:
        return None
