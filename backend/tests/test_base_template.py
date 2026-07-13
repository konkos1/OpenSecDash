from pathlib import Path
import re
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape


def test_navigation_orders_core_and_plugin_links_consistently():
    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("base.html")

    html = template.render(
        request=SimpleNamespace(url=SimpleNamespace(path="/")),
        language="en",
        domain="",
        timezone="auto",
        theme="dark",
        accent_color="blue",
        instance_logo_version=None,
        plugin_nav_items=[
            {"href": "/access", "label": "Access", "active_prefix": "/access", "order": 50},
            {"href": "/crowdsec", "label": "CrowdSec", "active_prefix": "/crowdsec", "order": 50},
        ],
        asset_plugins_enabled=True,
        event_plugins_enabled=True,
        app_version="test",
        update_available_version=None,
        live_page_refresh=False,
        backlog_datasources=[],
        current_user=None,
        can_operate=True,
        can_admin=True,
        t=lambda key: key,
    )

    desktop_nav = re.search(r'<nav class="nav-links-desktop gap-2 text-sm"[^>]*>(.*?)</nav>', html, re.DOTALL)
    mobile_nav = re.search(r'<nav class="nav-links-mobile px-4 pb-4 grid gap-2".*?>(.*?)</nav>', html, re.DOTALL)

    assert desktop_nav is not None
    assert mobile_nav is not None
    expected = ["/", "/access", "/crowdsec", "/events", "/rollups", "/assets", "/notifications", "/diagnostics", "/settings"]
    assert re.findall(r'href="([^"]+)"', desktop_nav.group(1)) == expected
    assert re.findall(r'href="([^"]+)"', mobile_nav.group(1)) == expected
    assert 'action="/search"' in mobile_nav.group(1)
    assert "data-navigation-header" in html
    assert "data-navigation-row" in html
    assert "data-navigation-brand" in html
    assert "data-navigation-primary" in html
    navigation_script = '<script src="/static/js/app.js?v=test-navigation-before-content"></script>'
    assert html.index("</header>") < html.index(navigation_script) < html.index("<main")
    assert html.count(navigation_script) == 1


def test_uploaded_instance_logo_stays_with_left_brand():
    css = Path("app/static/css/app.css").read_text()

    instance_logo_rule = re.search(r"\.instance-logo\s*\{([^}]*)\}", css)

    assert instance_logo_rule is not None
    assert "margin-right: auto" in instance_logo_rule.group(1)
