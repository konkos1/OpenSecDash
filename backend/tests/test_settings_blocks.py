import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.core.template_context import get_setting_value
from app.database.base import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.settings import Setting
from app.plugins.base import Plugin, PluginMetadata, PluginSetting
from app.plugins.manager import get_plugin_manager
from app.services.settings import save_setting
from app.web import auth as auth_web


class _FormNestingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_depth = 0
        self.has_nested_form = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.has_nested_form = self.has_nested_form or self.form_depth > 0
            self.form_depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_depth -= 1


@pytest.fixture()
def settings_client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings-blocks.db'}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_factory()

    def get_test_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(auth_web, "SessionLocal", session_factory)
    monkeypatch.setattr("app.main.SessionLocal", session_factory)
    monkeypatch.setattr("app.main.init_db", lambda: None)
    app.dependency_overrides[get_db] = get_test_db
    auth_api.reset_login_backoff()
    client = TestClient(app, base_url="https://testserver")
    try:
        yield db, client
    finally:
        client.close()
        app.dependency_overrides.clear()
        auth_api.reset_login_backoff()
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_settings_blocks_are_independent_and_use_non_nested_forms(settings_client):
    db, client = settings_client
    db.add_all(
        [
            Setting(key="domain", value="before.example"),
            Setting(key="notifications.enabled", value="false"),
            Setting(key="asset_updates.github_interval", value="21600"),
            Setting(key="plugin.crowdsec.enabled", value="true"),
        ]
    )
    db.commit()
    page = client.get("/settings")
    core_response = client.post(
        "/settings/core",
        data={
            "language": "de",
            "live_default": "false",
            "theme": "light",
            "accent_color": "green",
            "live_page_refresh": "false",
            "timezone": "Europe/Berlin",
        },
        follow_redirects=False,
    )
    branding_response = client.post("/settings/branding", data={"domain": "after.example"}, follow_redirects=False)
    notification_response = client.post("/settings/notifications", data={"notifications_enabled": "true"}, follow_redirects=False)
    asset_response = client.post(
        "/settings/asset-updates",
        data={"asset_updates_github_token": "test-token", "asset_updates_github_interval": "7200"},
        follow_redirects=False,
    )

    parser = _FormNestingParser()
    parser.feed(page.text)
    assert parser.has_nested_form is False
    assert core_response.status_code == 303
    assert branding_response.status_code == 303
    assert notification_response.status_code == 303
    assert asset_response.status_code == 303
    assert db.query(Setting).filter_by(key="domain").one().value == "after.example"
    assert db.query(Setting).filter_by(key="language").one().value == "de"
    assert db.query(Setting).filter_by(key="live_default").one().value == "false"
    assert db.query(Setting).filter_by(key="theme").one().value == "light"
    assert db.query(Setting).filter_by(key="instance_accent_color").one().value == "green"
    assert db.query(Setting).filter_by(key="live_page_refresh").one().value == "false"
    assert db.query(Setting).filter_by(key="timezone").one().value == "Europe/Berlin"
    assert db.query(Setting).filter_by(key="notifications.enabled").one().value == "true"
    assert get_setting_value(db, "asset_updates.github_token") == "test-token"
    assert db.query(Setting).filter_by(key="asset_updates.github_interval").one().value == "7200"
    assert db.query(Setting).filter_by(key="plugin.crowdsec.enabled").one().value == "true"
    assert 'action="/settings"' not in page.text
    assert 'name="language"' in page.text
    assert 'name="live_default"' in page.text
    assert 'name="theme"' in page.text
    assert 'name="accent_color"' in page.text
    assert 'name="live_page_refresh"' in page.text
    assert 'id="settings-core-form"' in page.text
    assert 'hx-select="#settings-core-form"' in page.text
    assert 'id="settings-notifications-form"' in page.text
    assert 'hx-select="#settings-notifications-form"' in page.text
    assert 'id="settings-asset-updates-form"' in page.text
    assert 'hx-select="#settings-asset-updates-form"' in page.text
    assert 'id="settings-plugin-crowdsec-form"' in page.text
    assert 'hx-select="#settings-plugin-crowdsec-form"' in page.text
    assert page.text.count('data-unsaved-warning="Discard unsaved settings changes?"') >= 4
    assert page.text.count("data-save-feedback") >= 4


def test_settings_details_open_only_core_and_place_users_after_branding(settings_client):
    _, client = settings_client
    page = client.get("/settings")

    assert page.text.count('<details class="card mb-5" open>') == 1
    assert page.text.index("Instance Branding") < page.text.index("Sign-in &amp; users")


def test_geoip_transport_warning_belongs_to_the_provider_option(settings_client):
    db, client = settings_client
    db.add(Setting(key="language", value="de"))
    db.commit()

    page = client.get("/settings")

    assert "GeoIP aktiviert <button" in page.text
    assert "GeoIP aktiviert (sendet" not in page.text
    # No stored provider row: the declarative default pre-selects IPLocate.
    assert '<option value="iplocate" selected>IPLocate EU-Endpunkt (verschlüsseltes HTTPS)</option>' in page.text
    assert '<option value="ip-api" >ip-api.com (unverschlüsselt)</option>' in page.text
    assert (
        '<small class="info-text block text-sm mt-1" '
        'x-show="settings[\'plugin.geoip.provider\'] === \'iplocate\'" x-cloak>'
        "Vollständige GeoIP-Anreicherung mit Land, Stadt, ASN und Provider/ISP. Nicht gecachte öffentliche "
        "IP-Adressen werden HTTPS-verschlüsselt an den EU-Endpunkt von IPLocate übertragen.</small>"
    ) in page.text
    assert (
        '<small class="info-text block text-sm mt-1" '
        'x-show="settings[\'plugin.geoip.provider\'] === \'ip-api\'" x-cloak>'
        "Nicht gecachte öffentliche IPs werden unverschlüsselt an ip-api.com gesendet.</small>"
    ) in page.text


def test_geoip_provider_options_and_transport_notes_exist_in_english_too(settings_client):
    db, client = settings_client
    db.add(Setting(key="language", value="en"))
    db.commit()

    page = client.get("/settings").text

    assert '<option value="iplocate" selected>IPLocate EU endpoint (encrypted HTTPS)</option>' in page
    assert '<option value="ip-api" >ip-api.com (unencrypted HTTP)</option>' in page
    assert (
        '<small class="info-text block text-sm mt-1" '
        "x-show=\"settings['plugin.geoip.provider'] === 'iplocate'\" x-cloak>"
        "Full GeoIP enrichment with country, city, ASN, and provider/ISP. Uncached public IP addresses are sent "
        "over encrypted HTTPS to IPLocate&#39;s EU endpoint.</small>"
    ) in page
    assert (
        '<small class="info-text block text-sm mt-1" '
        "x-show=\"settings['plugin.geoip.provider'] === 'ip-api'\" x-cloak>"
        "Uncached public IPs are sent to ip-api.com over unencrypted HTTP.</small>"
    ) in page
    # The transport note belongs to its own option: neither provider carries the other's.
    assert page.count("over encrypted HTTPS to IPLocate") == 1
    assert page.count("over unencrypted HTTP.") == 1


def test_iplocate_key_field_needs_enabled_geoip_and_the_iplocate_provider(settings_client):
    db, client = settings_client

    page = client.get("/settings").text

    assert (
        ":class=\"settings['plugin.geoip.enabled'] === 'true' "
        "&& settings['plugin.geoip.provider'] === 'iplocate' ? '' : 'settings-disabled'\""
    ) in page
    assert (
        ":readonly=\"!(settings['plugin.geoip.enabled'] === 'true' "
        "&& settings['plugin.geoip.provider'] === 'iplocate')\""
    ) in page

    # Enabling GeoIP and storing the key in one post is allowed ...
    client.post(
        "/settings/plugins/geoip",
        data={
            "plugin.geoip.enabled": "true",
            "plugin.geoip.provider": "iplocate",
            "plugin.geoip.iplocate_api_key": "dummy-iplocate-key",
        },
    )
    db.expire_all()
    stored = db.query(Setting).filter_by(key="plugin.geoip.iplocate_api_key").one().value

    assert get_setting_value(db, "plugin.geoip.iplocate_api_key") == "dummy-iplocate-key"
    assert stored.startswith("enc:v1:")
    assert "dummy-iplocate-key" not in stored

    # ... and the stored key is never rendered back into the page.
    page = client.get("/settings").text
    assert "dummy-iplocate-key" not in page
    assert "/settings/plugins/geoip/secrets/iplocate_api_key/delete" in page

    # A tampered post while ip-api is selected neither writes nor deletes it.
    client.post(
        "/settings/plugins/geoip",
        data={
            "plugin.geoip.enabled": "true",
            "plugin.geoip.provider": "ip-api",
            "plugin.geoip.iplocate_api_key": "dummy-tampered-key",
        },
    )
    hidden_delete = client.post("/settings/plugins/geoip/secrets/iplocate_api_key/delete", follow_redirects=False)
    db.expire_all()

    assert hidden_delete.status_code == 303
    assert get_setting_value(db, "plugin.geoip.provider") == "ip-api"
    assert get_setting_value(db, "plugin.geoip.iplocate_api_key") == "dummy-iplocate-key"

    # The same applies while GeoIP is off, even with iplocate selected.
    client.post(
        "/settings/plugins/geoip",
        data={
            "plugin.geoip.enabled": "false",
            "plugin.geoip.provider": "iplocate",
            "plugin.geoip.iplocate_api_key": "dummy-tampered-key",
        },
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.geoip.iplocate_api_key") == "dummy-iplocate-key"

    # ... and to the fourth combination, GeoIP off with ip-api selected.
    client.post(
        "/settings/plugins/geoip",
        data={
            "plugin.geoip.enabled": "false",
            "plugin.geoip.provider": "ip-api",
            "plugin.geoip.iplocate_api_key": "dummy-tampered-key",
        },
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.geoip.iplocate_api_key") == "dummy-iplocate-key"

    # Back in the valid state the key can be replaced and deliberately deleted.
    client.post(
        "/settings/plugins/geoip",
        data={
            "plugin.geoip.enabled": "true",
            "plugin.geoip.provider": "iplocate",
            "plugin.geoip.iplocate_api_key": "dummy-rotated-key",
        },
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.geoip.iplocate_api_key") == "dummy-rotated-key"

    deleted = client.post("/settings/plugins/geoip/secrets/iplocate_api_key/delete", follow_redirects=False)
    db.expire_all()
    assert deleted.status_code == 303
    assert get_setting_value(db, "plugin.geoip.iplocate_api_key") == ""


def test_plugin_settings_refresh_desktop_and_mobile_navigation(settings_client):
    _, client = settings_client

    enabled = client.post(
        "/settings/plugins/crowdsec",
        data={"plugin.crowdsec.enabled": "true"},
        headers={"HX-Request": "true"},
    )
    disabled = client.post(
        "/settings/plugins/crowdsec",
        data={"plugin.crowdsec.enabled": "false"},
        headers={"HX-Request": "true"},
    )

    assert enabled.status_code == 200
    assert disabled.status_code == 200
    for navigation_id in ("navigation-primary", "navigation-mobile"):
        enabled_navigation = re.search(
            rf'<nav[^>]*id="{navigation_id}"[^>]*hx-swap-oob="innerHTML"[^>]*>(.*?)</nav>',
            enabled.text,
            re.DOTALL,
        )
        disabled_navigation = re.search(
            rf'<nav[^>]*id="{navigation_id}"[^>]*hx-swap-oob="innerHTML"[^>]*>(.*?)</nav>',
            disabled.text,
            re.DOTALL,
        )
        assert enabled_navigation is not None
        assert disabled_navigation is not None
        assert 'href="/crowdsec"' in enabled_navigation.group(1)
        assert 'href="/crowdsec"' not in disabled_navigation.group(1)


class TwoConditionSecretPlugin(Plugin):
    """Synthetic plugin whose secret needs two conditions at once."""

    metadata = PluginMetadata(id="two_conditions", name="Two Conditions")
    settings = [
        PluginSetting("enabled", "tc.enabled", "tc.enabled.help", type="boolean", default="false"),
        PluginSetting(
            "mode",
            "tc.mode",
            "tc.mode.help",
            type="select",
            default="basic",
            options=[("basic", "tc.mode.basic"), ("advanced", "tc.mode.advanced")],
        ),
        PluginSetting(
            "api_key",
            "tc.api_key",
            "tc.api_key.help",
            type="password",
            default="",
            visible_if_all=(("enabled", "true"), ("mode", "advanced")),
        ),
    ]
    locales = {"en": {}, "de": {}}


@pytest.fixture()
def two_condition_plugin(monkeypatch):
    plugin = TwoConditionSecretPlugin()
    monkeypatch.setitem(get_plugin_manager().plugins, "two_conditions", plugin)
    return plugin


def test_plugin_secret_is_never_rendered_back_and_can_be_kept_replaced_and_deleted(settings_client):
    db, client = settings_client
    db.add(Setting(key="plugin.crowdsec.enabled", value="true"))
    db.commit()

    client.post(
        "/settings/plugins/crowdsec",
        data={"plugin.crowdsec.enabled": "true", "plugin.crowdsec.lapi_password": "dummy-lapi-secret"},
    )
    db.expire_all()
    stored = db.query(Setting).filter_by(key="plugin.crowdsec.lapi_password").one().value

    assert stored.startswith("enc:v1:")
    assert "dummy-lapi-secret" not in stored

    page = client.get("/settings")
    assert "dummy-lapi-secret" not in page.text
    assert "Stored - leave empty to keep it" in page.text
    assert '/settings/plugins/crowdsec/secrets/lapi_password/delete' in page.text

    # An empty field keeps the stored secret ...
    client.post(
        "/settings/plugins/crowdsec",
        data={"plugin.crowdsec.enabled": "true", "plugin.crowdsec.lapi_password": ""},
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.crowdsec.lapi_password") == "dummy-lapi-secret"

    # ... a new value replaces it, still encrypted ...
    client.post(
        "/settings/plugins/crowdsec",
        data={"plugin.crowdsec.enabled": "true", "plugin.crowdsec.lapi_password": "dummy-lapi-rotated"},
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.crowdsec.lapi_password") == "dummy-lapi-rotated"
    assert db.query(Setting).filter_by(key="plugin.crowdsec.lapi_password").one().value.startswith("enc:v1:")

    # ... and the plugin itself still receives the plaintext value.
    manager = get_plugin_manager()
    assert manager.context(db, manager.plugins["crowdsec"]).get("lapi_password") == "dummy-lapi-rotated"

    deleted = client.post("/settings/plugins/crowdsec/secrets/lapi_password/delete", follow_redirects=False)
    db.expire_all()
    assert deleted.status_code == 303
    assert get_setting_value(db, "plugin.crowdsec.lapi_password") == ""
    assert "Stored - leave empty to keep it" not in client.get("/settings").text


@pytest.mark.parametrize(
    ("plugin_id", "secret_key"),
    [("mqtt", "password"), ("proxmox_assets", "token_secret")],
)
def test_plugin_secret_of_a_disabled_plugin_cannot_be_set_or_deleted(settings_client, plugin_id, secret_key):
    db, client = settings_client
    full_key = f"plugin.{plugin_id}.{secret_key}"
    save_setting(db, full_key, "dummy-existing-secret")
    db.commit()

    client.post(
        f"/settings/plugins/{plugin_id}",
        data={f"plugin.{plugin_id}.enabled": "false", full_key: "dummy-injected-secret"},
    )
    deleted = client.post(f"/settings/plugins/{plugin_id}/secrets/{secret_key}/delete", follow_redirects=False)
    db.expire_all()

    assert deleted.status_code == 303
    assert get_setting_value(db, full_key) == "dummy-existing-secret"


def test_two_conditions_are_rendered_as_one_and_expression(settings_client, two_condition_plugin):
    _, client = settings_client

    page = client.get("/settings").text

    assert (
        ":class=\"settings['plugin.two_conditions.enabled'] === 'true' "
        "&& settings['plugin.two_conditions.mode'] === 'advanced' ? '' : 'settings-disabled'\""
    ) in page
    assert (
        ":readonly=\"!(settings['plugin.two_conditions.enabled'] === 'true' "
        "&& settings['plugin.two_conditions.mode'] === 'advanced')\""
    ) in page


def test_a_secret_behind_two_conditions_needs_both_of_them(settings_client, two_condition_plugin):
    db, client = settings_client

    # Only one condition met: the posted secret is dropped.
    client.post(
        "/settings/plugins/two_conditions",
        data={
            "plugin.two_conditions.enabled": "true",
            "plugin.two_conditions.mode": "basic",
            "plugin.two_conditions.api_key": "dummy-tampered-key",
        },
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.two_conditions.api_key") == ""

    # Both conditions arrive with the same post: activating and configuring works.
    client.post(
        "/settings/plugins/two_conditions",
        data={
            "plugin.two_conditions.enabled": "true",
            "plugin.two_conditions.mode": "advanced",
            "plugin.two_conditions.api_key": "dummy-key",
        },
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.two_conditions.api_key") == "dummy-key"

    # Turning the plugin off in the same post keeps the secret unwritable.
    client.post(
        "/settings/plugins/two_conditions",
        data={
            "plugin.two_conditions.enabled": "false",
            "plugin.two_conditions.api_key": "dummy-tampered-key",
        },
    )
    db.expire_all()
    assert get_setting_value(db, "plugin.two_conditions.enabled") == "false"
    assert get_setting_value(db, "plugin.two_conditions.api_key") == "dummy-key"
