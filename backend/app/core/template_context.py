from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.i18n import translate
from app.core.version import get_app_version, is_newer_version
from app.models.core import Datasource
from app.models.settings import Setting

PLUGIN_IDS = ["json_assets", "proxmox_assets", "crowdsec", "geoblock_log", "traefik_log", "mqtt", "mqtt-hass", "geoip"]


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


def enabled_plugin_map(db: Session) -> dict[str, bool]:
    values = get_setting_values(db, {f"plugin.{plugin_id}.enabled": "false" for plugin_id in PLUGIN_IDS})
    return {plugin_id: values[f"plugin.{plugin_id}.enabled"] == "true" for plugin_id in PLUGIN_IDS}


def build_template_context(db: Session) -> dict[str, object | Callable[[str], str]]:
    values = get_setting_values(
        db,
        {
            "language": "en",
            "domain": "",
            "timezone": "auto",
            "theme": "auto",
            "live_page_refresh": "true",
            "update_check_enabled": "true",
            "update_check.latest_version": "",
            **{f"plugin.{plugin_id}.enabled": "false" for plugin_id in PLUGIN_IDS},
        },
    )
    language = values["language"]
    domain = values["domain"]
    timezone = values["timezone"]
    theme = values["theme"]
    live_page_refresh = values["live_page_refresh"] == "true"
    enabled_plugins = {plugin_id: values[f"plugin.{plugin_id}.enabled"] == "true" for plugin_id in PLUGIN_IDS}
    event_plugins_enabled = any(
        enabled_plugins[plugin_id]
        for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"]
    )
    asset_plugins_enabled = any(
        enabled_plugins[plugin_id]
        for plugin_id in ["json_assets", "proxmox_assets"]
    )
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
        "live_page_refresh": live_page_refresh,
        "enabled_plugins": enabled_plugins,
        "event_plugins_enabled": event_plugins_enabled,
        "asset_plugins_enabled": asset_plugins_enabled,
        "backlog_datasources": backlog_datasources,
        "app_version": app_version,
        "update_available_version": update_available_version,
        "t": lambda key: translate(key, language),
    }
