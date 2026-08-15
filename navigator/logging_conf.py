"""Structured logging.

Log records carry machine-readable context (stage, run id, counts) as key=value
pairs rather than being baked into prose, so a run can be grepped or shipped to
a log aggregator without parsing English. Logs go to stderr; the human-facing
tables go to stdout, so `navigator run 2>/dev/null` still prints a clean report.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class KeyValueFormatter(logging.Formatter):
    """Renders `logger.info("msg", extra={...})` as `msg key=value ...`."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.getMessage()}"
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in RESERVED and not key.startswith("_")
        }
        if extras:
            rendered = " ".join(f"{k}={_format_value(v)}" for k, v in extras.items())
            return f"{base} {rendered}"
        return base


def _format_value(value: Any) -> str:
    text = str(value)
    return f'"{text}"' if " " in text else text


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(KeyValueFormatter())

    root = logging.getLogger("navigator")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"navigator.{name}")
