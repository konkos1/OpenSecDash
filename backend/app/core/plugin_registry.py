from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Dependency-free on purpose: core modules (template_context, i18n, pages, main)
# read this to answer "which plugins exist / have capability X" without importing
# the plugin manager, which would be circular (the manager imports those modules).
# The manager populates it once during discovery.


# A nav entry as flat tuple (label_key, href, active_prefix, order); flat so
# the registry stays free of any app.plugins.web import.
NavItem = tuple[str, str, str, int]


@dataclass(frozen=True)
class RegisteredPlugin:
    id: str
    name: str
    capabilities: tuple[str, ...]
    event_types: tuple[str, ...] = ()
    nav_items: tuple[NavItem, ...] = ()


_plugins: dict[str, RegisteredPlugin] = {}


def register_plugins(plugins: Iterable[RegisteredPlugin]) -> None:
    _plugins.clear()
    for plugin in plugins:
        _plugins[plugin.id] = plugin


def plugin_ids() -> list[str]:
    return list(_plugins)


def ids_with_capability(capability: str) -> list[str]:
    return [p.id for p in _plugins.values() if capability in p.capabilities]


def ids_producing_event_type(event_type: str) -> list[str]:
    """Return loaded plugins that declare they can emit an event type."""
    return [plugin.id for plugin in _plugins.values() if event_type in plugin.event_types]


def plugin_name(plugin_id: str) -> str:
    plugin = _plugins.get(plugin_id)
    return plugin.name if plugin is not None else plugin_id


def nav_items_by_plugin() -> dict[str, tuple[NavItem, ...]]:
    return {pid: p.nav_items for pid, p in _plugins.items() if p.nav_items}


def is_registered(plugin_id: str) -> bool:
    return plugin_id in _plugins
