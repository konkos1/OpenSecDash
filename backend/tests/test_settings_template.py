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
        timezone="auto",
        domain="homelab.example",
        enabled_plugins={},
        event_plugins_enabled=False,
        t=lambda key: key,
        plugin_settings_state={},
        plugin_setting_groups=[],
        language_setting="en",
        retention_days="30",
        live_default="true",
        asset_source_type="file",
        asset_source="",
        github_token="",
        action_dry_run="true",
        log_file_enabled="true",
        log_file_path="logs/opensecdash.log",
        log_level="INFO",
    )

    assert '<option value="light" selected>settings.theme_light</option>' in html
    assert '<option value="auto" selected>' not in html
