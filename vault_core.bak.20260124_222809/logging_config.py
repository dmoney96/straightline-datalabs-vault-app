from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import LOG_DIR


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger that:
      - writes to logs/<name>.log with rotation
      - also prints to stderr so CLI tools show logs_live
    """
    logger = logging.getLogger(name)

    # Avoid attaching handlers twice
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB per file
        backupCount=3,
        encoding="utf-8",
    )

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger
