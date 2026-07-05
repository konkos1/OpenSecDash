from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.settings import settings

logger = logging.getLogger(__name__)

# Storage format marker. A versioned prefix makes encrypted values
# self-describing: reads can decrypt regardless of which key list produced
# them, plaintext legacy values are trivially detectable (no prefix), and a
# future algorithm change becomes "enc:v2:" instead of a data migration.
ENCRYPTED_PREFIX = "enc:v1:"

# Same word list the log redaction and the debug report already use to decide
# what counts as sensitive. Matching on the settings key (not a hand-picked
# list of full key names) means a new plugin's "...password"/"...token"
# setting is protected automatically instead of silently stored in plaintext
# until someone remembers to register it somewhere.
SENSITIVE_KEY_MARKERS = ("password", "passwd", "pwd", "secret", "token", "apikey", "api_key", "access_key", "credential")

SECRET_KEY_ENV = "OSD_SECRET_KEY"
SECRET_KEY_FILENAME = "opensecdash.secret"

_cached_keys: list[bytes] | None = None


def is_sensitive_setting_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def _secret_key_path() -> Path:
    # Keep the auto-generated key next to the SQLite file (e.g. /data in
    # Docker): it survives container recreation because it lives on the same
    # volume. The documented upgrade for stricter setups is OSD_SECRET_KEY,
    # which keeps the key out of that volume (and out of its backups)
    # entirely.
    database_url = settings.database_url
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        db_path = Path(database_url.removeprefix("sqlite:///"))
        if database_url.startswith("sqlite:////"):
            db_path = Path("/" + database_url.removeprefix("sqlite:////"))
        return db_path.parent / SECRET_KEY_FILENAME
    return Path(SECRET_KEY_FILENAME)


def _load_keys() -> list[bytes]:
    """Return all usable keys, primary first.

    The primary key (used for every new encryption) is OSD_SECRET_KEY when
    set, otherwise the auto-generated key file next to the database. When the
    env key is set AND a key file exists - the documented "start simple,
    harden later" upgrade, or a deliberate rotation - the file key stays in
    the list as a decrypt-only fallback, so values encrypted before the
    switch keep working instead of silently reading as unset. The startup
    rotation pass (see rotate_encrypted_settings) then re-encrypts them under
    the primary key.
    """
    env_key = (os.environ.get(SECRET_KEY_ENV) or "").strip()
    key_path = _secret_key_path()

    keys: list[bytes] = []
    if env_key:
        keys.append(env_key.encode("utf-8"))
        if key_path.exists():
            keys.append(key_path.read_bytes().strip())
        return keys

    if key_path.exists():
        return [key_path.read_bytes().strip()]

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.touch(mode=0o600)
    key_path.write_bytes(key + b"\n")
    key_path.chmod(0o600)
    logger.info("Generated new secret key for settings encryption at %s", key_path)
    return [key]


def _keys() -> list[bytes]:
    global _cached_keys
    if _cached_keys is None:
        _cached_keys = _load_keys()
    return _cached_keys


def _fernet() -> MultiFernet:
    # MultiFernet encrypts with the FIRST key and tries all keys when
    # decrypting - exactly the primary-plus-fallback semantics needed for
    # seamless key rotation.
    return MultiFernet([Fernet(key) for key in _keys()])


def _primary_fernet() -> Fernet:
    return Fernet(_keys()[0])


def reset_secret_key_cache() -> None:
    """Drop the cached keys (tests, or after rotating OSD_SECRET_KEY)."""
    global _cached_keys
    _cached_keys = None


def encrypt_setting_value(key: str, value: str) -> str:
    """Encrypt a settings value if its key marks it as sensitive.

    Empty values and already-encrypted values pass through unchanged, so the
    call is safe on every write path including re-saving unchanged settings.
    """
    if not value or not is_sensitive_setting_key(key) or value.startswith(ENCRYPTED_PREFIX):
        return value
    return ENCRYPTED_PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_setting_value(key: str, value: str | None) -> str:
    """Decrypt a stored settings value; plaintext values pass through.

    A value that fails to decrypt with every available key (key file deleted,
    or rotated without the old key present as fallback) is treated as unset
    rather than crashing every page that reads settings: the plugin then
    behaves as unconfigured and the user re-enters the secret, which
    re-encrypts it with the current primary key.
    """
    if not value or not value.startswith(ENCRYPTED_PREFIX):
        return value or ""
    token = value.removeprefix(ENCRYPTED_PREFIX)
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning(
            "Could not decrypt setting %s - the encryption key has changed or the key file is missing. "
            "Re-enter the value in Settings to store it under the current key.",
            key,
        )
        return ""


def rotate_encrypted_value(value: str) -> str | None:
    """Re-encrypt an ``enc:v1:`` value under the current primary key.

    Returns the new stored value, or ``None`` when nothing needs to change:
    already readable with the primary key, not an encrypted value at all, or
    not decryptable with any available key (nothing sensible to rotate to).
    The explicit primary-key check matters because Fernet output is
    randomized - blindly calling rotate() would rewrite every secret on
    every startup instead of only the ones still under an old key.
    """
    if not value or not value.startswith(ENCRYPTED_PREFIX):
        return None
    token = value.removeprefix(ENCRYPTED_PREFIX).encode("ascii")
    try:
        _primary_fernet().decrypt(token)
        return None  # already under the primary key
    except (InvalidToken, ValueError):
        pass
    try:
        rotated = _fernet().rotate(token)
    except (InvalidToken, ValueError):
        return None  # no available key can read it; re-entering is the only fix
    return ENCRYPTED_PREFIX + rotated.decode("ascii")
