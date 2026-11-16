"""Structured key=value logging with a request-id contextvar (spec §6.2)."""

from __future__ import annotations

import contextvars
import logging
import sys
import time
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_CONFIGURED = False


class KeyValueFormatter(logging.Formatter):
    """`ts=… level=info logger=… request_id=… msg="…" key=value …`"""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"ts={time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(record.created))}Z "
            f"level={record.levelname.lower()} logger={record.name} "
            f"request_id={request_id_var.get()}"
        )
        message = record.getMessage()
        extras = getattr(record, "kv", None)
        if extras:
            rendered = " ".join(f"{key}={value}" for key, value in extras.items())
            return f'{base} msg="{message}" {rendered}'
        return f'{base} msg="{message}"'

    def formatException(self, ei: Any) -> str:  # noqa: N802 - stdlib signature
        return "\n" + super().formatException(ei)


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KeyValueFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn keeps its own access log; route it through the same formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"aragon.{name}")


def log_kv(logger: logging.Logger, level: int, message: str, **kv: Any) -> None:
    """Emit a structured line; `None` values are dropped — but `0`/`False`/`""`
    are real measurements (pct=0, costKnown=False) and stay in the line."""
    cleaned = {key: value for key, value in kv.items() if value is not None}
    logger.log(level, message, extra={"kv": cleaned})
