from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.events import Event
from app.services.events import store_event

# Keep this module intentionally dependency-light: external plugins import it as
# their public API surface. Changes here should be backwards compatible or paired
# with an ADR/plugin API version bump.
#
# Note on the "page" capability: it is currently declarative only - plugin
# pages and their integration services are still wired up in core
# (app/api/pages.py, app/services/). See ADR-044 for the interim convention
# and the goal of plugins owning their services and registering pages here.
PluginCapability = Literal["datasource", "enrichment", "action", "export", "page", "widget", "insight"]
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
