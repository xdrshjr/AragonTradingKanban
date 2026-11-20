"""Structured key=value logging with a request-id contextvar (spec §6.2)."""

from __future__ import annotations

import contextvars
import logging
import re
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


#: ★v15（§24.3.5 / AC-163）：日志里**永不**出现明文密码、会话令牌、`Set-Cookie` 值、Bearer。
#: 这是第二道锁——代码本就不该把它们交给 logger；过滤器兜的是"某天有人 `logger.info(request.json())`"。
#: 打码的是**值**，键名保留，所以 `password=***` 仍能告诉排障的人"这里有一个密码字段"。
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # key=value / key: value / "key": "value"，值可带引号。
    re.compile(
        r"""(?ix)
        (\b(?:password|passwd|pwd|currentPassword|newPassword|aragon_sid|session[_-]?(?:token|id)?|
              token|secret|api[_-]?key|captchaAnswer|captcha_answer|recovery[_-]?key)\b["']?\s*[=:]\s*)
        ("[^"]*"|'[^']*'|[^\s,;&"'}]+)
        """
    ),
    # `Authorization: Bearer xxx` / `Bearer xxx` / `Set-Cookie: aragon_sid=xxx`
    re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]+)"),
)
REDACTED = "***"
#: A structured-log (`kv`) *key* that names a secret: its value is masked wholesale.
_SENSITIVE_KEY = re.compile(
    r"(?i)(password|passwd|pwd|currentPassword|newPassword|aragon_sid|session[_-]?(?:token|id)?|token|secret|"
    r"api[_-]?key|captchaAnswer|captcha_answer|recovery[_-]?key|authorization|cookie)"
)


def redact(text: str) -> str:
    """Mask the value of every secret-looking `key=value` / `Bearer …` fragment in `text`."""
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    return text


class RedactFilter(logging.Filter):
    """Rewrite a record's message (and `kv` extras) with secrets masked. Never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a malformed %-format must not kill logging
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        extras = getattr(record, "kv", None)
        if isinstance(extras, dict):
            record.kv = {
                key: REDACTED
                if _SENSITIVE_KEY.fullmatch(str(key))
                else (redact(value) if isinstance(value, str) else value)
                for key, value in extras.items()
            }
        return True


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KeyValueFormatter())
    handler.addFilter(RedactFilter())
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
