import logging

from app.core.logging import FILE_HANDLER_NAME, SERVICE_HANDLER_NAME, configure_logging_from_db, setup_service_logging
from app.models.settings import Setting


def _file_handlers():
    return [handler for handler in logging.getLogger().handlers if getattr(handler, "_opensecdash_name", None) == FILE_HANDLER_NAME]


def test_configure_logging_replaces_file_handler_when_path_changes(db_session, tmp_path):
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    root.handlers = []
    try:
        first_log = tmp_path / "first" / "opensecdash.log"
        second_log = tmp_path / "second" / "opensecdash.log"
        db_session.add_all(
            [
                Setting(key="log_file_enabled", value="true"),
                Setting(key="log_file_path", value=str(first_log)),
                Setting(key="log_level", value="INFO"),
            ]
        )
        db_session.commit()

        setup_service_logging()
        configure_logging_from_db(db_session)
        handlers = _file_handlers()
        assert len(handlers) == 1
        assert getattr(handlers[0], "baseFilename") == str(first_log)
        assert any(getattr(handler, "_opensecdash_name", None) == SERVICE_HANDLER_NAME for handler in root.handlers)

        db_session.query(Setting).filter_by(key="log_file_path").one().value = str(second_log)
        db_session.commit()
        configure_logging_from_db(db_session)

        handlers = _file_handlers()
        assert len(handlers) == 1
        assert getattr(handlers[0], "baseFilename") == str(second_log)
        assert any(getattr(handler, "_opensecdash_name", None) == SERVICE_HANDLER_NAME for handler in root.handlers)
        logging.getLogger("app.test").info("message after path change")
        handlers[0].flush()
        assert second_log.exists()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)
