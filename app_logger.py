import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

from platform_runtime import logs_dir


_LOGGER_NAME = "quick_audio_recorder"
_APP_FORMAT = "%(asctime)s | %(levelname)s | %(threadName)s | %(name)s | %(message)s"
_OUTPUT_HANDLER_KEY = "_win_rec_app_output_handler_path"
_LEVEL_ENV_VAR = "WIN_REC_LOG_LEVEL"
# 5MB rotation keeps a typical multi-hour session bounded yet still
# lets us reconstruct a few weeks of usage from 5 backup files.
_ROTATION_MAX_BYTES = 5 * 1024 * 1024
_ROTATION_BACKUP_COUNT = 5


def _log_dir() -> str:
    return logs_dir()


def _resolve_log_level() -> int:
    raw = (os.getenv(_LEVEL_ENV_VAR) or "").strip().upper()
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    return mapping.get(raw, logging.INFO)


def _mask_home(path: Optional[str]) -> str:
    """Replace the user's home directory with ``~`` for safer log sharing.

    Returns ``""`` for falsy input so callers can safely interpolate into
    structured log lines without an extra ``or ""`` guard.
    """
    if not path:
        return ""
    try:
        text = str(path)
        home = os.path.expanduser("~")
        if home and text.startswith(home):
            return "~" + text[len(home):]
        return text
    except Exception:
        return str(path)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    level = _resolve_log_level()
    logger.setLevel(level)
    logger.propagate = False

    log_file = os.path.join(_log_dir(), "app.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=_ROTATION_MAX_BYTES,
        backupCount=_ROTATION_BACKUP_COUNT,
        encoding="utf-8",
    )
    formatter = logging.Formatter(_APP_FORMAT)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.info(
        "logger_ready | level=%s | file=%s | rotation=5MB,5",
        logging.getLevelName(level),
        _mask_home(log_file),
    )
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
        maxBytes=_ROTATION_MAX_BYTES,
        backupCount=_ROTATION_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_APP_FORMAT))
    setattr(file_handler, _OUTPUT_HANDLER_KEY, log_path)
    logger.addHandler(file_handler)
    logger.info(
        "output_folder_log_enabled | path=%s | rotation=5MB,5",
        _mask_home(log_path),
    )
    return log_path


def get_logger() -> logging.Logger:
    return setup_logging()
