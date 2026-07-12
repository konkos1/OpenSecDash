from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core import plugin_registry
from app.core.i18n import translate
from app.core.version import get_app_version, is_newer_version
from app.models.core import Datasource
from app.models.settings import Setting
from app.services.instance_branding import instance_file_versions


def get_setting_value(
    db: Session,
    key: str,
    default: str = "",
) -> str:
    setting = (
        db.query(Setting)
        .filter(Setting.key == key)
        .first()
    )

    if setting is None:
        return default

    # Sensitive values are stored encrypted (see app.core.secrets); decrypting
    # here means every consumer - plugins, pages, background loops - keeps
    # reading plaintext without knowing about encryption at all.
    from app.core.secrets import decrypt_setting_value

    return decrypt_setting_value(key, setting.value)


def get_setting_values(db: Session, defaults: dict[str, str]) -> dict[str, str]:
    """Fetch many settings in one query; missing keys keep their default.

    The template context needs a dozen settings for every page, fragment and
    auto-refresh poll - loading them one by one was a dozen query round trips
    per request. Per-key semantics match get_setting_value exactly.
    """
    from app.core.secrets import decrypt_setting_value

    values = dict(defaults)
    for row in db.query(Setting).filter(Setting.key.in_(defaults)).all():
        values[row.key] = decrypt_setting_value(row.key, row.value)
    return values


def build_template_context(db: Session) -> dict[str, object | Callable[[str], str]]:
    values = get_setting_values(
        db,
        {
            "language": "en",
            "domain": "",
            "instance_description": "",
            "timezone": "auto",
            "theme": "auto",
            "instance_accent_color": "blue",
            "live_page_refresh": "true",
            "update_check_enabled": "true",
            "update_check.latest_version": "",
            **{f"plugin.{plugin_id}.enabled": "false" for plugin_id in plugin_registry.plugin_ids()},
        },
    )
    language = values["language"]
    domain = values["domain"]
    timezone = values["timezone"]
    theme = values["theme"]
    accent_color = values["instance_accent_color"]
    if accent_color not in {"blue", "green", "orange", "red"}:
        accent_color = "blue"
    file_versions = instance_file_versions(db)
    live_page_refresh = values["live_page_refresh"] == "true"
    enabled_plugins = {plugin_id: values[f"plugin.{plugin_id}.enabled"] == "true" for plugin_id in plugin_registry.plugin_ids()}
    event_plugins_enabled = any(
        enabled_plugins.get(plugin_id)
        for plugin_id in plugin_registry.ids_with_capability("datasource")
    )
    asset_plugins_enabled = any(
        enabled_plugins.get(plugin_id)
        for plugin_id in plugin_registry.ids_with_capability("asset_source")
    )
    # Nav entries contributed by plugins that register a web surface (phase 6+);
    # only shown for enabled plugins. Empty until a plugin provides nav items.
    plugin_nav_items = [
        {"href": href, "label": translate(label_key, language), "active_prefix": active_prefix, "order": order}
        for plugin_id, items in plugin_registry.nav_items_by_plugin().items()
        if enabled_plugins.get(plugin_id)
        for (label_key, href, active_prefix, order) in items
    ]
    plugin_nav_items.sort(key=lambda item: (item["order"], item["href"]))
    backlog_datasources = (
        db.query(Datasource)
        .filter(Datasource.enabled == True, Datasource.backlog_pending == True)  # noqa: E712
        .order_by(Datasource.name)
        .all()
    )
    app_version = get_app_version()
    # The background self-update check stores the latest GitHub release in
    # settings; rendering only ever compares strings - no network calls in
    # the request path. Respect the toggle even when a cached value exists.
    latest_known_version = values["update_check.latest_version"]
    update_available_version = (
        latest_known_version
        if values["update_check_enabled"] == "true" and is_newer_version(latest_known_version, app_version)
        else None
    )

    return {
        "language": language,
        "domain": domain,
        "timezone": timezone,
        "theme": theme,
        "accent_color": accent_color,
        "instance_description": values["instance_description"],
        "instance_logo_version": file_versions["logo"],
        "instance_favicon_version": file_versions["favicon"],
        "live_page_refresh": live_page_refresh,
        "enabled_plugins": enabled_plugins,
        "event_plugins_enabled": event_plugins_enabled,
        "asset_plugins_enabled": asset_plugins_enabled,
        "plugin_nav_items": plugin_nav_items,
        "backlog_datasources": backlog_datasources,
        "app_version": app_version,
        "update_available_version": update_available_version,
        "t": lambda key: translate(key, language),
    }
