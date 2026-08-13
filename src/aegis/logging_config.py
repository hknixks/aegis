"""Logging setup for Aegis.

A single entry point configures the root logger once, honoring
AEGIS_LOG_LEVEL and AEGIS_LOG_FORMAT from Settings. Call configure_logging()
before doing any other work (e.g. at the top of the CLI).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from aegis.config import Settings

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(settings: Settings) -> None:
    """Configure the root logger exactly once per process."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler()
    if settings.aegis_log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )

    root = logging.getLogger()
    root.setLevel(settings.aegis_log_level.upper())
    root.handlers.clear()
    root.addHandler(handler)

    _CONFIGURED = True
