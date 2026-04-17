import logging
import os
from logging.handlers import RotatingFileHandler


_LOGGER_NAME = "quick_audio_recorder"


def _log_dir() -> str:
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "QuickAudioRecorder", "logs")
    os.makedirs(path, exist_ok=True)
    return path


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
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


def get_logger() -> logging.Logger:
    return setup_logging()
