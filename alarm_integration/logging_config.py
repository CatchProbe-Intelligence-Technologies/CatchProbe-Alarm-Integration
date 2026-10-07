"""Centralised logging configuration.

Keeps logging setup in one place so the CLI, the package entry point and any
embedding application share a consistent format.
"""

from __future__ import annotations

import logging
import os

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str | int | None = None) -> None:
    """Configure the root logger once.

    Args:
        level: Logging level as a name (``"INFO"``) or numeric value. When
            ``None`` the ``LOG_LEVEL`` environment variable is used, defaulting
            to ``INFO``.
    """
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = logging.getLevelName(level.upper())

    logging.basicConfig(level=level, format=_DEFAULT_FORMAT, datefmt=_DATE_FORMAT)
