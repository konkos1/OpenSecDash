from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.core.template_context import get_setting_value

SERVICE_HANDLER_NAME = "opensecdash-service"
FILE_HANDLER_NAME = "opensecdash-file"
DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}
SENSITIVE_WORDS = ("password", "passwd", "pwd", "secret", "token", "apikey", "api_key", "access_key", "auth", "credential")
SECRET_PATTERNS = [
    re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization|bearer)\s*[:=]\s*([^\s,;]+)"),
]
URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+")


def _redact_url(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        parts = urlsplit(url)
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        if parts.username:
            netloc = f"<redacted>@{netloc}"
        query_items = [
            (key, "<redacted>" if any(word in key.lower() for word in SENSITIVE_WORDS) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        query = urlencode(query_items)
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except Exception:
        return "<redacted-url>"


def redact_sensitive(value: object) -> str:
    text = URL_PATTERN.sub(_redact_url, str(value))
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}<redacted>" if m.group(1).lower().startswith("authorization") and ":" in m.group(1) else f"{m.group(1)}=<redacted>", text)
    return text


def display_logger_name(name: str) -> str:
    if name == "root":
        return "app.root"
    if name.startswith("opensecdash_external_plugin_"):
        plugin_name = name.removeprefix("opensecdash_external_plugin_").removesuffix(".plugin")
        return f"plugins.{plugin_name}"
    return name


class RedactingFormatter(logging.Formatter):
    """Formatter used by service and file logging.

    It normalizes logger names for readable/colorable output and redacts secrets
    at the last possible point so callers do not need to duplicate redaction.
    """

    def format(self, record: logging.LogRecord) -> str:
        original_name = record.name
        record.name = display_logger_name(record.name)
        try:
            return redact_sensitive(super().format(record))
        finally:
            record.name = original_name


def _formatter() -> logging.Formatter:
    return RedactingFormatter(DEFAULT_FORMAT)


def _level(value: str | None) -> int:
    return LEVELS.get(str(value or "INFO").upper(), logging.INFO)


def setup_service_logging(level: str = "INFO") -> None:
    """Always log to the service console so systemd/journalctl can capture it.

    This runs before DB settings are available. ``configure_logging_from_db`` may
    later adjust levels and add file logging, but must never remove this handler.
    """
    root = logging.getLogger()
    root.setLevel(_level(level))
    for name, logger_obj in logging.Logger.manager.loggerDict.items():
        if name.startswith(("app", "opensecdash_external_plugin_")) and isinstance(logger_obj, logging.Logger):
            logger_obj.disabled = False
            logger_obj.setLevel(logging.NOTSET)
    for handler in root.handlers:
        if getattr(handler, "_opensecdash_name", None) == SERVICE_HANDLER_NAME:
            handler.setLevel(_level(level))
            return
    handler = logging.StreamHandler()
    handler._opensecdash_name = SERVICE_HANDLER_NAME  # type: ignore[attr-defined]
    handler.setFormatter(_formatter())
    handler.setLevel(_level(level))
    root.addHandler(handler)


def configure_logging_from_db(db: Session) -> None:
    log_level = get_setting_value(db, "log_level", "INFO").upper()
    file_enabled = get_setting_value(db, "log_file_enabled", "true").lower() == "true"
    log_path = get_setting_value(db, "log_file_path", "logs/opensecdash.log")

    root = logging.getLogger()
    root.setLevel(_level(log_level))
    setup_service_logging(log_level)

    for handler in list(root.handlers):
        if getattr(handler, "_opensecdash_name", None) == FILE_HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()

    if not file_enabled:
        logging.getLogger(__name__).info("File logging disabled; service logging remains active")
        return

    try:
        path = Path(log_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path)
        handler._opensecdash_name = FILE_HANDLER_NAME  # type: ignore[attr-defined]
        handler.setFormatter(_formatter())
        handler.setLevel(_level(log_level))
        root.addHandler(handler)
        logging.getLogger(__name__).info("File logging enabled at %s with level %s", path, log_level)
    except Exception:
        logging.getLogger(__name__).exception("Could not enable file logging at %s", log_path)
