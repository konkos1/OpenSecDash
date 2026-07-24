"""Persisting individual settings rows, encrypted and change-logged.

This is the write counterpart to ``app.core.template_context.get_setting_value``
and lives in the service layer so both web routes and other services can reuse
it without depending on the web layer.
"""
import logging

from sqlalchemy.orm import Session

from app.core.logging import redacted_setting_value
from app.core.secrets import decrypt_setting_value, encrypt_setting_value
from app.models.settings import Setting

logger = logging.getLogger(__name__)


def save_setting(db: Session, key: str, value: str) -> None:
    # Sensitive values (passwords, tokens, ...) are encrypted at rest; the
    # comparison below runs on the decrypted value so re-saving an unchanged
    # secret doesn't produce a new ciphertext (Fernet output is randomized)
    # and a misleading "Setting changed" log line on every settings save.
    stored_value = encrypt_setting_value(key, value)
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting is None:
        db.add(Setting(key=key, value=stored_value))
        logger.info("Setting created key=%s value=%s", key, redacted_setting_value(key, value))
    elif decrypt_setting_value(key, setting.value) != value:
        old_value = decrypt_setting_value(key, setting.value)
        setting.value = stored_value
        logger.info(
            "Setting changed key=%s old=%s new=%s",
            key,
            redacted_setting_value(key, old_value),
            redacted_setting_value(key, value),
        )
    else:
        logger.debug("Setting unchanged key=%s", key)
