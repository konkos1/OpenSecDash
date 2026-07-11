# Developing plugins

OpenSecDash is intentionally plugin-first. The core app provides storage, settings rendering, diagnostics, actions, rollups, enrichment hooks, and UI pages. Plugins add integrations for specific tools or environments.

This is how OpenSecDash can grow beyond the built-in Traefik, CrowdSec, GeoBlock, Proxmox, JSON Assets, GeoIP, and MQTT integrations.

## Plugin location

External plugins live in the repository-level `plugins/` directory:

```text
plugins/<plugin_id>/
  __init__.py
  plugin.py
  routes.py          # optional web/API routes
  templates/         # optional namespaced Jinja templates
  locales.py         # optional translation module
  services/          # optional integration-specific logic
```

A plugin directory must contain `__init__.py` and a `plugin.py` file exposing a class named `Plugin`. Plugin-internal imports should be relative, for example `from .services.parser import parse_line`, because plugins are loaded under OpenSecDash's synthetic plugin namespace rather than installed as normal Python packages.

Example:

```text
plugins/my_firewall/__init__.py
plugins/my_firewall/plugin.py
```

The plugin manager discovers plugins by scanning `plugins/*/plugin.py`.

## Minimal plugin structure

```python
from __future__ import annotations

from app.plugins.base import DatasourcePlugin, PluginMetadata, PluginSetting


class Plugin(DatasourcePlugin):
    metadata = PluginMetadata(
        id="my_firewall",
        name="My Firewall",
        version="1.0.0",
        api_version="2",
        capabilities=["datasource"],
        description="Imports firewall events from a log file.",
    )

    settings = [
        PluginSetting(
            "enabled",
            "my_firewall.settings.enabled",
            "my_firewall.settings.enabled.help",
            "boolean",
            "false",
            [("false", "common.no"), ("true", "common.yes")],
        ),
        PluginSetting(
            "log_path",
            "my_firewall.settings.log_path",
            "my_firewall.settings.log_path.help",
            "file",
            "/var/log/my-firewall.log",
        ),
    ]

    locales = {
        "en": {
            "my_firewall.settings.enabled": "My Firewall enabled",
            "my_firewall.settings.enabled.help": "Imports events from My Firewall.",
            "my_firewall.settings.log_path": "Log path",
            "my_firewall.settings.log_path.help": "Path to the firewall log file.",
            "common.yes": "Yes",
            "common.no": "No",
        },
        "de": {
            "my_firewall.settings.enabled": "My Firewall aktiviert",
            "my_firewall.settings.enabled.help": "Importiert Events aus My Firewall.",
            "my_firewall.settings.log_path": "Log-Pfad",
            "my_firewall.settings.log_path.help": "Pfad zur Firewall-Logdatei.",
            "common.yes": "Ja",
            "common.no": "Nein",
        },
    }
```

## Plugin metadata

`PluginMetadata` describes the integration:

| Field | Meaning |
| --- | --- |
| `id` | Stable technical plugin ID. Use lowercase snake_case. It becomes part of setting keys and diagnostics IDs. |
| `name` | Human-readable plugin name. |
| `version` | Plugin version. |
| `description` | Short description shown in diagnostics/debug output. |
| `author` | Optional author name. |
| `api_version` | Plugin API version. Use `"2"` for package-layout plugins with `__init__.py`, relative imports, optional route/template hooks, action hooks, and `asset_source` support. |
| `capabilities` | List of plugin capabilities. |

Current capability values:

| Capability | Use for |
| --- | --- |
| `datasource` | Imports events from logs/APIs. |
| `asset_source` | Imports or synchronizes assets/systems into the central asset model. |
| `enrichment` | Enriches existing data. |
| `action` | Executes actions such as bans/unbans. |
| `export` | Exports data to another system, such as MQTT. |
| `page` | Powers a dedicated UI page. |
| `widget` | Contributes dashboard/widgets. |
| `insight` | Produces insights or insight-like context. |

## Settings

Plugins declare settings with `PluginSetting`. OpenSecDash renders them automatically on the Settings page.

```python
PluginSetting(
    key="poll_interval",
    label_key="my_plugin.settings.poll_interval",
    help_key="my_plugin.settings.poll_interval.help",
    type="number",
    default="5",
)
```

Supported setting types:

| Type | UI behavior |
| --- | --- |
| `text` | Text input. |
| `password` | Password input; values are redacted in debug output. |
| `number` | Numeric text input. |
| `boolean` | Select with configured options. |
| `select` | Select with configured options. |
| `file` | Text/file path input with existence diagnostics when enabled. |
| `url` | URL input; values are sanitized when saved. |

If a plugin has an `enabled` setting, other settings are automatically greyed out until the plugin is enabled, unless you define an explicit `visible_if`. This keeps available configuration visible without making inactive options look editable.

Settings are stored as:

```text
plugin.<plugin_id>.<setting_key>
```

For example:

```text
plugin.my_firewall.log_path
```

## Locales

Settings use translation keys, not literal UI strings. Each plugin should provide at least English strings and preferably German strings too.

```python
locales = {
    "en": {
        "my_plugin.settings.enabled": "My plugin enabled",
        "my_plugin.settings.enabled.help": "Enables My plugin.",
    },
    "de": {
        "my_plugin.settings.enabled": "My Plugin aktiviert",
        "my_plugin.settings.enabled.help": "Aktiviert My Plugin.",
    },
}
```

## Plugin types and hooks

Plugins inherit from one or more base classes in `app.plugins.base`.

### Dashboard widgets

Plugins with dashboard content implement `dashboard_widgets()` and declare the
`widget` capability in `PluginMetadata`. The hook returns `DashboardWidget` descriptors
from `app.web.dashboard`; it does not return HTML, templates, or callables. The
descriptor type is one of `counter`, `table`, `feed`, or `trend`, and links use internal
paths so the core can validate them before rendering. Check the plugin's `enabled`
setting in the hook and return an empty list when it is disabled.

Package-layout plugins use `api_version="2"`, for example:

```python
from app.core.template_context import get_setting_value
from app.plugins.base import DatasourcePlugin
from app.web.dashboard import DashboardWidget


class Plugin(DatasourcePlugin):
    def dashboard_widgets(self, db) -> list[DashboardWidget]:
        if get_setting_value(db, "plugin.my_firewall.enabled", "false") != "true":
            return []
        return [
            DashboardWidget(
                id="my_firewall.blocked_today",
                type="counter",
                section="security",
                title_key="my_firewall.dashboard.blocked_today",
                value=0,
                href="/events?event_type=security.firewall_block&today=true",
            )
        ]
```

Use the existing `DashboardWidget` model for fields and row shapes; do not invent a
plugin-specific schema. Add matching translation keys for `title_key` and keep all
data structured so the core templates escape it safely.

### Insight rules

Plugins can optionally return a declarative insight ruleset. The core imports it during
database seeding, validates it with the same schema as bundled and remote rules, and
stores it with the source `plugin:<plugin_id>`. Return data only: do not return Python
code, callables, templates, or a remote URL.

```python
from typing import Any


class Plugin(DatasourcePlugin):
    def insight_rules(self) -> dict[str, Any] | None:
        return {
            "schema_version": 1,
            "ruleset_version": "2026-07-11",
            "rules": [
                {
                    "id": "my_firewall.login_probe",
                    "title": "Repeated login probe",
                    "event_types": ["access.denied"],
                    "path_contains_any": ["/login"],
                    "group_by": "ip",
                    "window_minutes": 5,
                    "threshold": 3,
                }
            ],
        }
```

An invalid ruleset is reported as a plugin diagnostic warning and does not stop other
plugins from seeding. Rules for plugins that are no longer loaded are deactivated.

### `DatasourcePlugin`

Use this for plugins that import events.

```python
class Plugin(DatasourcePlugin):
    async def collect(self, context):
        return [
            {
                "source": "My Firewall",
                "source_id": "my-firewall-log",
                "plugin": self.metadata.id,
                "plugin_id": self.metadata.id,
                "event_type": "security.firewall_block",
                "severity": "warning",
                "ip": "203.0.113.42",
                "raw_data": "original log line",
            }
        ]
```

The plugin manager stores returned events through OpenSecDash's central ingestion path. That means deduplication, GeoIP enrichment, rollups, host-to-asset linking, and insights can run consistently.

### `PeriodicPlugin`

Use this for periodic work that is not simply returning datasource events, for example API syncs.

```python
class Plugin(PeriodicPlugin):
    async def tick(self, context):
        # sync external API, cleanup cache, etc.
        return None
```

Sleep interval is derived from common settings such as `poll_interval` or `publish_interval` when present.

### `ActionPlugin`

Use this for actions triggered by OpenSecDash, for example a ban/unban backend.

Declare each action as an `ActionDefinition`. The registry metadata drives the available-actions API, confirmation dialog, audit record/event, and IP Explorer button:

```python
from app.plugins.base import ActionDefinition, ActionParameter, ActionPlugin


class Plugin(ActionPlugin):
    action_definitions = (
        ActionDefinition(
            action_type="security.ban",
            label_key="ip.crowdsec_ban",
            description_key="action.desc.security.ban",
            target_types=frozenset({"ip"}),
            critical=True,
            permission="security.ban",
            parameters=(
                ActionParameter(
                    name="duration",
                    options=("4h", "24h", "7d"),
                    default="4h",
                    label_key="action.param.duration",
                ),
            ),
        ),
    )

    async def execute(self, context, action_type, target, parameters):
        ...

    def validate_action(self, db, action_type, target, parameters, dry_run):
        return parameters

    def action_available(self, db, action_type, target, dry_run):
        return True
```

The core exposes the registered actions at `GET /api/actions/available`. `target_types`, `critical`, `permission`, and `parameters` describe where an action is offered and how its form is rendered. `execute`, `validate_action`, and `action_available` remain plugin hooks; central confirmation, IP validation, dry-run handling, action records, and audit events stay in the core. The CrowdSec plugin uses the same pattern for its Ban and Unban definitions.

```python
class Plugin(ActionPlugin):
    async def execute(self, context, action_type, target, parameters):
        if action_type != "security.ban":
            return None
        return {"status": "ok", "target": target}
```

Return `None` when the action is not handled by your plugin.

### `ExportPlugin`

Use this to export events or assets to another system.

```python
class Plugin(ExportPlugin):
    async def export_asset(self, context, asset):
        # publish asset update state
        return None
```

## Plugin context

Hooks receive a `PluginContext` object.

| API | Meaning |
| --- | --- |
| `context.db` | SQLAlchemy session for advanced reads/writes. Prefer central helpers when available. |
| `context.get(key, default)` | Reads this plugin's settings without the `plugin.<id>.` prefix. |
| `context.emit_event(**values)` | Stores one event through the central ingestion path. |
| `context.export_asset_update(asset, manual=False)` | Calls export plugins for an asset update. |
| `context.manual_export` | Whether an export was manually triggered. |

## Event taxonomy

Use stable, predictable event types. Existing examples:

```text
access.allowed
access.denied
access.error
security.ban
security.geoblock
action.executed
action.failed
```

For new plugins, prefer namespaced event types:

```text
security.firewall_block
security.vpn_login_failed
access.proxy_error
```

Recommended event fields:

| Field | Meaning |
| --- | --- |
| `source` | Human-readable source name. |
| `source_id` | Stable source identifier. |
| `plugin` / `plugin_id` | Your plugin ID. |
| `event_type` | Stable event type. |
| `severity` | `info`, `warning`, `error`, or `critical`. |
| `ip` | Source/client IP when available. |
| `hostname` | Host/service name when available. |
| `method`, `path`, `status_code` | HTTP fields for access-like events. |
| `data_json` | Structured plugin-specific payload. |
| `raw_data` | Original log line or raw payload for deduplication/debugging. |

## Health checks and diagnostics

Implement `health()` to make Diagnostics useful:

```python
async def health(self, context):
    path = Path(context.get("log_path"))
    if not path.exists():
        return {"status": "error", "message": f"Log file not found: {path}"}
    return {"status": "healthy", "message": f"Log file readable: {path}"}
```

Statuses should be:

```text
healthy
warning
error
```

When a plugin is disabled, OpenSecDash reports it as disabled without calling its normal work loop.

## Best practices

- Keep plugins small and focused.
- Do not block forever in plugin hooks.
- Use timeouts for network calls.
- Store secrets only in password settings.
- Put structured details in `data_json`.
- Include `raw_data` for log imports when possible.
- Avoid destructive actions unless dry-run and confirmation behavior are clear.
- Avoid broad event types that make filtering confusing.
- Add tests for parsers and action behavior.

## Testing a plugin

Run checks from the repository root/backend:

```bash
cd backend
uv run pytest -q
uv run pyright ../backend/app ../backend/tests ../plugins
```

For parser-heavy plugins, add focused unit tests that call `parse_line()` or equivalent helper methods directly.

## Current examples

Useful built-in examples:

| Plugin | What to learn from it |
| --- | --- |
| `plugins/traefik_log/plugin.py` + `routes.py` + `templates/` | JSON log parsing, page registration, and access event UI. |
| `plugins/geoblock_log/plugin.py` | Simple line-based log parser. |
| `plugins/crowdsec/plugin.py` + `services/` + `routes.py` | Log import, action execution via `cscli`, and plugin-owned UI/services. |
| `plugins/proxmox_assets/plugin.py` + `services/sync.py` | API-based periodic sync and plugin-owned service module. |
| `plugins/mqtt/plugin.py` + `routes.py` | Export plugin behavior and an ungated route with legacy setting fallback. |
