"""GitHub Actions-aware logging helpers.

Wraps stdlib logging so that warning/error records are formatted as GitHub
Actions workflow annotations (``::warning::`` / ``::error::``). All output goes
to stdout — GHA scans both streams for workflow commands, and keeping a single
stream simplifies test capture (``capsys``).
"""

from __future__ import annotations

import logging
import sys
from typing import ClassVar

_LOGGER_NAME = "devin_action"
_LOGGER = logging.getLogger(_LOGGER_NAME)


class _WorkflowCommandFormatter(logging.Formatter):
    _PREFIX: ClassVar[dict[int, str]] = {
        logging.WARNING: "::warning::",
        logging.ERROR: "::error::",
    }

    def format(self, record: logging.LogRecord) -> str:
        return f"{self._PREFIX.get(record.levelno, '')}{record.getMessage()}"


def _ensure_configured() -> None:
    if _LOGGER.handlers:
        return
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_WorkflowCommandFormatter())
    _LOGGER.addHandler(handler)


_ensure_configured()


def info(message: str) -> None:
    _LOGGER.info(message)


def warning(message: str) -> None:
    _LOGGER.warning(message)


def error(message: str) -> None:
    _LOGGER.error(message)


def mask(value: str) -> None:
    """Register a secret with GitHub Actions so it is scrubbed from logs."""
    if value:
        _LOGGER.info(f"::add-mask::{value}")
