from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.i18n import translate
from app.core.language import get_language
from app.models.settings import Setting


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

    return setting.value


def enabled_plugin_map(db: Session) -> dict[str, bool]:
    return {
        plugin_id: get_setting_value(db, f"plugin.{plugin_id}.enabled", "false") == "true"
        for plugin_id in ["apps_inventory", "proxmox_assets", "crowdsec", "geoblock_log", "traefik_log", "mqtt", "mqtt-hass", "geoip"]
    }


def build_template_context(db: Session) -> dict[str, object | Callable[[str], str]]:
    language = get_language(db)
    domain = get_setting_value(db, "domain", "")
    timezone = get_setting_value(db, "timezone", "auto")
    theme = get_setting_value(db, "theme", "auto")
    enabled_plugins = enabled_plugin_map(db)
    event_plugins_enabled = any(
        enabled_plugins[plugin_id]
        for plugin_id in ["crowdsec", "geoblock_log", "traefik_log"]
    )
    asset_plugins_enabled = any(
        enabled_plugins[plugin_id]
        for plugin_id in ["apps_inventory", "proxmox_assets"]
    )

    return {
        "language": language,
        "domain": domain,
        "timezone": timezone,
        "theme": theme,
        "enabled_plugins": enabled_plugins,
        "event_plugins_enabled": event_plugins_enabled,
        "asset_plugins_enabled": asset_plugins_enabled,
        "t": lambda key: translate(key, language),
    }
