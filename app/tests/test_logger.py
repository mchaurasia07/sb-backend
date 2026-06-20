import logging

from app.core import logger as logger_module
from app.core.config import settings


def test_configure_logging_writes_to_configured_logs_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOG_TO_FILE", True)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "LOG_FILE_NAME", "storybook-backend-test.log")
    monkeypatch.setattr(settings, "LOG_FILE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(settings, "LOG_FILE_BACKUP_COUNT", 1)

    logger_module.configure_logging()

    log = logger_module.get_logger("TEST-PROCESS")
    log.info("file_logging_test", marker="scheduler-log-file")

    log_path = tmp_path / "logs" / "storybook-backend-test.log"
    assert log_path.exists()
    log_line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert " - INFO - [TEST-PROCESS] - file_logging_test marker=scheduler-log-file" in log_line
    assert not log_line.startswith("{")

    logger_module.configure_logging()


def test_configure_logging_formats_standard_logging_records(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOG_TO_FILE", True)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "LOG_FILE_NAME", "standard-logging-test.log")
    monkeypatch.setattr(settings, "LOG_FILE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(settings, "LOG_FILE_BACKUP_COUNT", 1)

    logger_module.configure_logging()

    logging.getLogger("STANDARD-PROCESS").info("standard message workflow=%s", "abc-123")

    log_path = tmp_path / "logs" / "standard-logging-test.log"
    log_line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert " - INFO - [STANDARD-PROCESS] - standard message workflow=abc-123" in log_line
    assert not log_line.startswith("{")

    logger_module.configure_logging()


def test_configure_logging_can_disable_file_handler(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOG_TO_FILE", False)
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "LOG_FILE_NAME", "disabled.log")

    logger_module.configure_logging()

    root_handlers = logging.getLogger().handlers
    assert not any(
        isinstance(handler, logging.FileHandler)
        and getattr(handler, "baseFilename", "").endswith("disabled.log")
        for handler in root_handlers
    )

    logger_module.configure_logging()
