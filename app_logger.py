import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

from platform_runtime import logs_dir


_LOGGER_NAME = "quick_audio_recorder"
_APP_FORMAT = "%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"
_OUTPUT_HANDLER_KEY = "_win_rec_app_output_handler_path"


def _log_dir() -> str:
    return logs_dir()


def setup_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_file = os.path.join(_log_dir(), "app.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(_APP_FORMAT)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


def configure_output_folder_logging(output_folder: Optional[str]) -> Optional[str]:
    if not output_folder:
        return None

    logger = setup_logging()
    path = os.path.abspath(output_folder)
    os.makedirs(path, exist_ok=True)
    log_path = os.path.join(path, "app.log")

    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and getattr(
            handler, _OUTPUT_HANDLER_KEY, ""
        ):
            if getattr(handler, _OUTPUT_HANDLER_KEY) == log_path:
                return log_path
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_APP_FORMAT))
    setattr(file_handler, _OUTPUT_HANDLER_KEY, log_path)
    logger.addHandler(file_handler)
    logger.info("Enabled recording-folder log output: %s", log_path)
    return log_path


def get_logger() -> logging.Logger:
    return setup_logging()
