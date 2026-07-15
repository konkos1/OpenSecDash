from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape


def test_settings_template_uses_independent_details_forms():
    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("settings.html")

    html = template.render(
        request=SimpleNamespace(url=SimpleNamespace(path="/settings")),
        language="en",
        theme="light",
        accent_color="blue",
        timezone="auto",
        domain="homelab.example",
        enabled_plugins={},
        event_plugins_enabled=False,
        asset_plugins_enabled=False,
        app_version="test",
        t=lambda key: key,
        plugin_settings_state={},
        plugin_setting_groups=[],
        global_language="en",
        global_live_default="true",
        global_theme="light",
        global_accent_color="blue",
        global_live_page_refresh="true",
        retention_days="30",
        asset_source_type="file",
        asset_source="",
        action_dry_run="true",
        log_file_enabled="true",
        log_file_path="logs/opensecdash.log",
        log_level="INFO",
        asset_updates_github_token="",
        asset_updates_github_interval="21600",
        notifications_enabled="false",
        notifications_base_url="",
        notifications_smtp_host="",
        notifications_smtp_port="587",
        notifications_smtp_security="starttls",
        notifications_smtp_user="",
        notifications_smtp_password="",
        notifications_smtp_sender="",
        notifications_smtp_recipient="",
        auth_enabled=False,
    )

    assert 'action="/settings/core"' in html
    assert 'action="/settings/branding"' in html
    assert 'action="/settings/notifications"' in html
    assert 'action="/settings/asset-updates"' in html
    assert 'name="theme"' in html
    assert '<div><button class="btn" type="submit">' not in html
    assert html.count('<div class="md:col-span-2 flex items-end"><button class="btn" type="submit">common.save_button</button></div>') == 4
    assert html.index('name="domain"') > html.index('action="/settings/branding"')
    assert '<details class="card mb-5" open>' in html
