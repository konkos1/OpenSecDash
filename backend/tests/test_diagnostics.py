from app.api.pages import diagnostic_disabled_message, diagnostic_plugin_enabled
from app.models.settings import Setting


def _set(db_session, key: str, value: str) -> None:
    existing = db_session.query(Setting).filter(Setting.key == key).first()
    if existing is None:
        db_session.add(Setting(key=key, value=value))
    else:
        existing.value = value
    db_session.commit()


def test_geoip_diagnostic_disabled_without_event_source(db_session):
    _set(db_session, "plugin.geoip.enabled", "true")

    assert diagnostic_plugin_enabled(db_session, "geoip") is False
    assert diagnostic_disabled_message(db_session, "geoip") == "No event datasource plugin is enabled."


def test_geoip_diagnostic_enabled_with_event_source(db_session):
    _set(db_session, "plugin.geoip.enabled", "true")
    _set(db_session, "plugin.traefik_log.enabled", "true")

    assert diagnostic_plugin_enabled(db_session, "geoip") is True


def test_geoip_diagnostic_disabled_whichever_provider_is_preselected(db_session):
    # A fresh installation has iplocate selected but GeoIP off: the diagnostic
    # must report the plugin as disabled, not as an active provider.
    _set(db_session, "plugin.geoip.enabled", "false")
    _set(db_session, "plugin.geoip.provider", "iplocate")
    _set(db_session, "plugin.traefik_log.enabled", "true")

    assert diagnostic_plugin_enabled(db_session, "geoip") is False
    assert diagnostic_disabled_message(db_session, "geoip") == "Plugin is disabled and not running."
