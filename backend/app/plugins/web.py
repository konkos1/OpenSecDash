from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter

# Web surface a plugin can register. Kept separate from app.plugins.base so
# base.py stays free of FastAPI/Jinja imports (plugins without pages never
# import this module).


@dataclass(frozen=True)
class PluginNavItem:
    label_key: str          # translated via the plugin's own locales (registered at discovery)
    href: str               # e.g. "/crowdsec"
    active_prefix: str      # nav highlighting: current_path.startswith(active_prefix)
    order: int = 50


@dataclass(frozen=True)
class PluginWebRegistration:
    router: APIRouter | None = None
    # Routes mounted WITHOUT the plugin-enabled guard; the route body does its
    # own gating. Needed e.g. for endpoints that must also work in dry-run
    # while the plugin is disabled (see phase 6, /actions/ip).
    ungated_router: APIRouter | None = None
    templates_dir: Path | None = None   # mounted as "<plugin_id>/<template name>"
    nav_items: tuple[PluginNavItem, ...] = ()
    ip_page_panels: tuple[str, ...] = field(default_factory=tuple)  # template names injected into the IP explorer (phase 6)
