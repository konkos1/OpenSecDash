from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape


def test_theme_dropdown_selects_saved_theme():
    env = Environment(
        loader=FileSystemLoader("app/templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("settings.html")

    html = template.render(
        request=SimpleNamespace(url=SimpleNamespace(path="/settings")),
        language="en",
        theme="light",
        instance_accent_color="blue",
        timezone="auto",
        domain="homelab.example",
        enabled_plugins={},
        event_plugins_enabled=False,
        asset_plugins_enabled=False,
        app_version="test",
        t=lambda key: key,
        plugin_settings_state={},
        plugin_setting_groups=[],
        language_setting="en",
        retention_days="30",
        live_default="true",
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
    )

    assert '<option value="light" selected>settings.theme_light</option>' in html
    assert '<option value="auto" selected>' not in html
    assert '<button class="sr-only" type="submit" aria-hidden="true" tabindex="-1">common.save_button</button>' in html
    assert html.index('class="sr-only"') < html.index('formaction="/settings/branding"')
