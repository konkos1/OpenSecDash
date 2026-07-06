from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# Synthetic namespace so plugin directories are importable as packages without
# plugins/ being on sys.path or installed. Works identically in the dev
# checkout, the Docker layout (/app/plugins) and tests. Plugin-internal
# imports must be relative (e.g. "from .services import decisions").
PLUGIN_NAMESPACE = "osd_plugins"


def _ensure_namespace() -> None:
    if PLUGIN_NAMESPACE not in sys.modules:
        namespace = ModuleType(PLUGIN_NAMESPACE)
        namespace.__path__ = []  # mark as package so submodule imports resolve
        sys.modules[PLUGIN_NAMESPACE] = namespace


def load_plugin_package(plugin_dir: Path) -> ModuleType:
    """Register ``plugin_dir`` as the package ``osd_plugins.<dirname>``."""
    _ensure_namespace()
    package_name = f"{PLUGIN_NAMESPACE}.{plugin_dir.name}"
    if package_name in sys.modules:
        return sys.modules[package_name]
    init_py = plugin_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        package_name, init_py, submodule_search_locations=[str(plugin_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load plugin package from {plugin_dir}")
    module = importlib.util.module_from_spec(spec)
    # The package must be in sys.modules before exec_module, or submodule
    # imports triggered by the package body would fail to resolve.
    sys.modules[package_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A half-initialized package in sys.modules would mask the real error
        # on the next import attempt.
        sys.modules.pop(package_name, None)
        raise
    return module


def import_plugin_module(plugin_dir: Path, submodule: str) -> ModuleType:
    """Import ``osd_plugins.<dirname>.<submodule>`` (e.g. "plugin", "services.decisions")."""
    load_plugin_package(plugin_dir)
    return importlib.import_module(f"{PLUGIN_NAMESPACE}.{plugin_dir.name}.{submodule}")
