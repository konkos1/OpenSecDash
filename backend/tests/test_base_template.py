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
        current_user=SimpleNamespace(username="alice"),
        can_operate=True,
        can_admin=True,
        t=lambda key: key,
    )

    desktop_nav = re.search(r'<nav class="nav-links-desktop gap-2 text-sm"[^>]*>(.*?)</nav>', html, re.DOTALL)
    mobile_nav = re.search(r'<nav class="nav-links-mobile px-4 pb-4 grid gap-2".*?>(.*?)</nav>', html, re.DOTALL)

    assert desktop_nav is not None
    assert mobile_nav is not None
    expected = ["/", "/access", "/crowdsec", "/events", "/rollups", "/assets", "/notifications", "/diagnostics", "/settings", "/account"]
    assert re.findall(r'href="([^"]+)"', desktop_nav.group(1)) == expected
    assert re.findall(r'href="([^"]+)"', mobile_nav.group(1)) == expected
    assert 'action="/search"' in mobile_nav.group(1)
    assert "data-navigation-header" in html
    assert "data-navigation-row" in html
    assert "data-navigation-brand" in html
    assert "data-navigation-primary" in html
    assert 'id="navigation-primary"' in html
    assert 'id="navigation-mobile"' in html
    assert 'hx-swap-oob="innerHTML"' not in html
    assert "navigationResizeObserver.observe(navigationPrimary)" in Path("app/static/js/app.js").read_text()
    assert html.count('class="icon user-icon"') == 2
    assert html.count('class="icon logout-icon"') == 2
    assert html.count('aria-label="alice" data-tooltip="alice"') == 2
    assert html.count('aria-label="auth.logout" data-tooltip="auth.logout"') == 2
    assert 'href="/legal">footer.licenses</a>' in html
    assert '/static/css/app.css?v=test-info-text-icons' in html
    navigation_script = '<script src="/static/js/app.js?v=test-nav-icon-tooltips"></script>'
    assert html.index("</header>") < html.index(navigation_script) < html.index("<main")
    assert html.count(navigation_script) == 1
    assert 'id="save-feedback-banner"' in html
    assert 'data-message="common.settings_saved"' in html


def test_help_tooltips_are_restored_after_htmx_refreshes():
    script = Path("app/static/js/app.js").read_text()

    assert "pendingTooltipRestore" in script
    assert '".help[data-tooltip]"' in script
    assert '".dashboard-trend-bar[data-chart-tooltip]"' in script
    assert '".nav-icon[data-tooltip]"' in script
    assert "showTriggerTooltip(trigger)" in script
    assert "trigger.focus({ preventScroll: true })" in script


def test_refreshable_details_are_restored_after_htmx_swaps():
    script = Path("app/static/js/app.js").read_text()

    assert 'target.querySelectorAll("details[data-refresh-state]")' in script
    assert "details.dataset.refreshState" in script
    assert "details.open" in script
    assert "detailsStatesByKey" in script


def test_live_refresh_dispatches_to_the_htmx_synchronized_result_region():
    script = Path("app/static/js/app.js").read_text()
    live_mode = script.split("function openSecDashLiveMode", 1)[1].split("function openSecDashAutoRefresh", 1)[0]

    assert 'htmx.trigger(results, "opensecdash-refresh")' in live_mode
    assert "htmx.ajax" not in live_mode


def test_uploaded_instance_logo_stays_with_left_brand():
    css = Path("app/static/css/app.css").read_text()

    instance_logo_rule = re.search(r"\.instance-logo\s*\{([^}]*)\}", css)

    assert instance_logo_rule is not None
    assert "margin-right: auto" in instance_logo_rule.group(1)


def test_page_width_reserves_space_for_late_scrollbars():
    css = Path("app/static/css/app.css").read_text()

    html_rule = re.search(r"html\s*\{([^}]*)\}", css)

    assert html_rule is not None
    assert "scrollbar-gutter: stable" in html_rule.group(1)


def test_mobile_navigation_does_not_override_alpine_open_state():
    css = Path("app/static/css/app.css").read_text()

    mobile_navigation_rule = re.search(r"(?m)^\.nav-links-mobile\s*\{", css)

    assert mobile_navigation_rule is None
    assert "[x-cloak] { display: none !important; }" in css
    assert ".navigation-expanded .nav-links-mobile { display: none !important; }" in css


def test_info_text_uses_the_shared_decorative_icon():
    css = Path("app/static/css/app.css").read_text()

    icon_rule = re.search(r"\.info-text::before\s*\{([^}]*)\}", css)

    assert icon_rule is not None
    assert 'content: ""' in icon_rule.group(1)
    assert "url('/static/img/info.svg')" in icon_rule.group(1)
    assert Path("app/static/img/info.svg").is_file()


def test_form_controls_keep_their_touch_target_size_with_tailwind_preflight():
    css = Path("app/static/css/app.css").read_text()

    input_rule = re.search(r'input\[type="file"\]\.input\s*\{([^}]*)\}', css)

    assert input_rule is not None
    assert "min-height: 3rem" in input_rule.group(1)
