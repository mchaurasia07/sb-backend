import json
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

    log = logger_module.get_logger("app.tests.test_logger")
    log.info("file_logging_test", marker="scheduler-log-file")

    log_path = tmp_path / "logs" / "storybook-backend-test.log"
    assert log_path.exists()
    log_line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    log_json = json.loads(log_line)
    assert log_json["event"] == "file_logging_test"
    assert log_json["marker"] == "scheduler-log-file"

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
