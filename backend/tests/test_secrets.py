import stat

from cryptography.fernet import Fernet

import app.core.secrets as secrets_module
from app.api.pages import save_setting
from app.core.secrets import (
    ENCRYPTED_PREFIX,
    decrypt_setting_value,
    encrypt_setting_value,
    is_sensitive_setting_key,
    reset_secret_key_cache,
)
from app.core.template_context import get_setting_value, get_setting_values
from app.database.init_db import encrypt_legacy_plaintext_secrets
from app.models.settings import Setting


def test_sensitive_key_detection_matches_the_redaction_word_list():
    assert is_sensitive_setting_key("asset_updates.github_token")
    assert is_sensitive_setting_key("plugin.proxmox_assets.token_secret")
    assert is_sensitive_setting_key("plugin.mqtt-hass.password")
    assert is_sensitive_setting_key("mqtt_password")
    assert not is_sensitive_setting_key("timezone")
    assert not is_sensitive_setting_key("plugin.traefik_log.log_path")


def test_encrypt_decrypt_roundtrip_and_prefix():
    stored = encrypt_setting_value("plugin.mqtt-hass.password", "hunter2")
    assert stored.startswith(ENCRYPTED_PREFIX)
    assert "hunter2" not in stored
    assert decrypt_setting_value("plugin.mqtt-hass.password", stored) == "hunter2"


def test_non_sensitive_empty_and_already_encrypted_values_pass_through():
    assert encrypt_setting_value("timezone", "Europe/Berlin") == "Europe/Berlin"
    assert encrypt_setting_value("plugin.mqtt-hass.password", "") == ""
    once = encrypt_setting_value("plugin.mqtt-hass.password", "hunter2")
    assert encrypt_setting_value("plugin.mqtt-hass.password", once) == once
    assert decrypt_setting_value("timezone", "Europe/Berlin") == "Europe/Berlin"
    assert decrypt_setting_value("plugin.mqtt-hass.password", None) == ""


def test_decrypt_with_rotated_key_returns_empty_instead_of_crashing(monkeypatch):
    stored = encrypt_setting_value("plugin.mqtt-hass.password", "hunter2")
    monkeypatch.setenv(secrets_module.SECRET_KEY_ENV, Fernet.generate_key().decode("ascii"))
    reset_secret_key_cache()

    assert decrypt_setting_value("plugin.mqtt-hass.password", stored) == ""


def test_save_setting_encrypts_at_rest_and_reads_back_plaintext(db_session):
    save_setting(db_session, "asset_updates.github_token", "ghp_supersecret")
    save_setting(db_session, "timezone", "Europe/Berlin")
    db_session.commit()

    raw_token = db_session.query(Setting).filter_by(key="asset_updates.github_token").one().value
    assert raw_token.startswith(ENCRYPTED_PREFIX)
    assert "ghp_supersecret" not in raw_token
    assert db_session.query(Setting).filter_by(key="timezone").one().value == "Europe/Berlin"

    assert get_setting_value(db_session, "asset_updates.github_token") == "ghp_supersecret"
    values = get_setting_values(db_session, {"asset_updates.github_token": "", "timezone": "auto"})
    assert values == {"asset_updates.github_token": "ghp_supersecret", "timezone": "Europe/Berlin"}


def test_resaving_unchanged_secret_keeps_the_stored_ciphertext(db_session):
    # Fernet output is randomized; without comparing decrypted values every
    # settings save would rewrite the row and log a bogus "Setting changed".
    save_setting(db_session, "asset_updates.github_token", "ghp_supersecret")
    db_session.commit()
    first = db_session.query(Setting).filter_by(key="asset_updates.github_token").one().value

    save_setting(db_session, "asset_updates.github_token", "ghp_supersecret")
    db_session.commit()
    second = db_session.query(Setting).filter_by(key="asset_updates.github_token").one().value

    assert first == second


def test_startup_migration_encrypts_legacy_plaintext_secrets(db_session):
    db_session.add(Setting(key="plugin.proxmox_assets.token_secret", value="legacy-plain"))
    db_session.add(Setting(key="timezone", value="Europe/Berlin"))
    db_session.commit()

    encrypt_legacy_plaintext_secrets(db_session)
    db_session.commit()

    raw = db_session.query(Setting).filter_by(key="plugin.proxmox_assets.token_secret").one().value
    assert raw.startswith(ENCRYPTED_PREFIX)
    assert get_setting_value(db_session, "plugin.proxmox_assets.token_secret") == "legacy-plain"
    assert db_session.query(Setting).filter_by(key="timezone").one().value == "Europe/Berlin"

    # Second run is a no-op (no double encryption).
    encrypt_legacy_plaintext_secrets(db_session)
    db_session.commit()
    assert get_setting_value(db_session, "plugin.proxmox_assets.token_secret") == "legacy-plain"


def test_switching_from_key_file_to_env_key_keeps_old_values_readable(monkeypatch, tmp_path):
    # "Start simple, harden later": values encrypted under the auto-generated
    # key file must keep decrypting after OSD_SECRET_KEY is introduced,
    # because the file key stays available as a decrypt-only fallback.
    monkeypatch.delenv(secrets_module.SECRET_KEY_ENV, raising=False)
    monkeypatch.setattr(secrets_module.settings, "database_url", f"sqlite:///{tmp_path}/opensecdash.db")
    reset_secret_key_cache()
    stored = encrypt_setting_value("plugin.mqtt-hass.password", "hunter2")

    monkeypatch.setenv(secrets_module.SECRET_KEY_ENV, Fernet.generate_key().decode("ascii"))
    reset_secret_key_cache()

    assert decrypt_setting_value("plugin.mqtt-hass.password", stored) == "hunter2"


def test_startup_pass_rotates_file_key_values_under_the_env_key(monkeypatch, tmp_path, db_session):
    monkeypatch.delenv(secrets_module.SECRET_KEY_ENV, raising=False)
    monkeypatch.setattr(secrets_module.settings, "database_url", f"sqlite:///{tmp_path}/opensecdash.db")
    reset_secret_key_cache()
    db_session.add(Setting(key="plugin.mqtt-hass.password", value=encrypt_setting_value("plugin.mqtt-hass.password", "hunter2")))
    db_session.commit()
    old_stored = db_session.query(Setting).filter_by(key="plugin.mqtt-hass.password").one().value

    # Container restart with OSD_SECRET_KEY set; the key file still exists.
    env_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv(secrets_module.SECRET_KEY_ENV, env_key)
    reset_secret_key_cache()
    encrypt_legacy_plaintext_secrets(db_session)
    db_session.commit()

    new_stored = db_session.query(Setting).filter_by(key="plugin.mqtt-hass.password").one().value
    assert new_stored != old_stored
    assert new_stored.startswith(ENCRYPTED_PREFIX)

    # The rotated value must be readable with the env key ALONE - even after
    # the old key file is gone (user deletes it, or a fresh volume).
    (tmp_path / secrets_module.SECRET_KEY_FILENAME).unlink()
    reset_secret_key_cache()
    assert decrypt_setting_value("plugin.mqtt-hass.password", new_stored) == "hunter2"

    # Re-running the pass is a no-op: values already under the primary key
    # are not rewritten (Fernet output is randomized, so a blind rotate
    # would churn every secret on every startup).
    encrypt_legacy_plaintext_secrets(db_session)
    db_session.commit()
    assert db_session.query(Setting).filter_by(key="plugin.mqtt-hass.password").one().value == new_stored


def test_key_file_is_autogenerated_next_to_the_database(monkeypatch, tmp_path):
    monkeypatch.delenv(secrets_module.SECRET_KEY_ENV, raising=False)
    monkeypatch.setattr(secrets_module.settings, "database_url", f"sqlite:///{tmp_path}/opensecdash.db")
    reset_secret_key_cache()

    stored = encrypt_setting_value("plugin.mqtt-hass.password", "hunter2")
    key_path = tmp_path / secrets_module.SECRET_KEY_FILENAME
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    # A fresh process (cache reset) reuses the same key file.
    reset_secret_key_cache()
    assert decrypt_setting_value("plugin.mqtt-hass.password", stored) == "hunter2"
