# ADR-044: Plugin Service Ownership

> **Implementation status (2026-07-09):** Implemented.
> The plugin service ownership rework is implemented; integration-specific services live in plugin packages except documented core exceptions.


## Status: implemented (2026-07-07)

The complete implementation plan is located in `docs/internal/plugin-rework/` and was implemented in phases 1 through 9. The earlier transitional convention of placing new integration-specific services under `backend/app/services/` is therefore **rescinded**.

New convention: Integration-specific services, pages, templates, and locales belong in the respective plugin directory (`plugins/<name>/services/`, `routes.py`, `templates/`, `locales.py`). The core does not directly access plugin modules; shared interaction runs through the plugin manager, registries, and plugin hooks.

Documented deviation from the original target outline: `json_assets_updates.py` was not moved into the `json_assets` plugin, but was kept in the core as `backend/app/services/asset_updates.py`. Update checks run across all assets with a release URL, independently of the source (including Proxmox assets), and therefore belong to the source-independent update detection framework (ADR-034).

Known remaining couplings that intentionally remain:

- `CrowdSecDecision` remains as a central schema in `app/models/core.py` (ADR-036); the CrowdSec service logic is in the plugin.
- The Asset Explorer (`/assets*`) remains core and renders central asset models as well as UI for plugin actions. This includes the `proxmox_error` error banner and MQTT legacy lookups for existing installations.
- The `widget` capability remains declarative; dashboard widgets continue to be core and use feature flags/capabilities.

## Context before implementation

Integration-specific domain logic that was also needed by core pages used to live in `backend/app/services/` instead of the respective plugin directory:

```none
app/services/crowdsec_decisions.py
app/services/crowdsec_lapi.py
app/services/proxmox_assets.py
app/services/json_assets_import.py
app/services/json_assets_source.py
app/services/json_assets_updates.py
```

This was a deliberate transitional solution: The previous plugin loader only loaded individual `plugin.py` files, plugins were not packages, and otherwise the core would have had to import concrete plugin modules. With the rework, plugins are loaded as packages under the synthetic namespace `osd_plugins`, can import their own submodules relatively, and register web interfaces through `Plugin.web()`.

## Decision

- Plugins own their integration-specific domain logic themselves.
- Plugins register their own routes, templates, navigation, and IP Explorer panels through the plugin API.
- The action framework remains plugin-agnostic and uses plugin hooks for validation, event data, dedupe, and follow-up work.
- The core imports no concrete plugin modules; it uses managers, registries, and hooks.
- Plugin API Version `2` describes the package layout with `__init__.py`, relative imports, optional web/IP/dedupe/action hooks, and the `asset_source` capability.
