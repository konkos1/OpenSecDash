from typing import Any, cast

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.core import plugin_registry
from app.core.template_context import build_template_context
from app.database.base import Base
from app.models.settings import Setting
from app.plugins.manager import get_plugin_manager
from app.web.guards import plugin_enabled_guard
from app.web.render import render
from app.web.templates import register_plugin_template_dirs, templates

PLUGIN_ID = "webtest"
NAV_ITEM = (("nav.webtest", "/webtest", "/webtest", 50),)


@pytest.fixture(autouse=True)
def _restore_globals():
    # These tests mutate process-global state (plugin registry + the shared
    # Jinja loader). Restore both afterwards so other test modules see the real
    # registry and the default template loader.
    original_loader = templates.env.loader
    yield
    templates.env.loader = original_loader
    get_plugin_manager().discover()


def _register_test_plugin(*, with_nav: bool = True):
    plugin_registry.register_plugins(
        [
            plugin_registry.RegisteredPlugin(
                id=PLUGIN_ID,
                name="Web Test",
                capabilities=(),
                nav_items=NAV_ITEM if with_nav else (),
            )
        ]
    )


def _memory_session(enabled: bool):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    session.add(Setting(key=f"plugin.{PLUGIN_ID}.enabled", value="true" if enabled else "false"))
    session.commit()
    return session


def _write_plugin_template(tmp_path):
    (tmp_path / "page.html").write_text(
        '{% extends "base.html" %}\n{% block content %}<div id="webtest-marker">WEBTEST_OK</div>{% endblock %}\n',
        encoding="utf-8",
    )
    register_plugin_template_dirs({PLUGIN_ID: str(tmp_path)})


def _fake_request(path: str = "/webtest") -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


def test_plugin_enabled_guard_blocks_when_disabled():
    # This is exactly what a mounted plugin router's dependency does: 404 while
    # the plugin is off, pass through when on.
    _register_test_plugin()
    guard = plugin_enabled_guard(PLUGIN_ID)

    with pytest.raises(HTTPException) as exc:
        guard(db=_memory_session(enabled=False))
    assert exc.value.status_code == 404

    assert guard(db=_memory_session(enabled=True)) is None


def test_plugin_namespaced_template_extends_base(tmp_path):
    _register_test_plugin()
    _write_plugin_template(tmp_path)

    response = render(_fake_request(), _memory_session(enabled=True), f"{PLUGIN_ID}/page.html")
    body = bytes(response.body).decode()

    # Namespaced plugin template rendered ...
    assert "WEBTEST_OK" in body
    # ... and it really extended the core base.html (nav chrome present).
    assert 'href="/settings"' in body


def test_plugin_nav_item_only_present_when_enabled():
    _register_test_plugin()

    enabled_items = cast(list[dict[str, Any]], build_template_context(_memory_session(enabled=True))["plugin_nav_items"])
    assert "/webtest" in [item["href"] for item in enabled_items]

    disabled_items = cast(list[dict[str, Any]], build_template_context(_memory_session(enabled=False))["plugin_nav_items"])
    assert "/webtest" not in [item["href"] for item in disabled_items]
