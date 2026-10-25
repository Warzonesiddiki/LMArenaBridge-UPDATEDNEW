"""Structured, redacting logging helpers for LMArenaBridge.

The historical code emits human-readable messages through ``debug_print``.
This module keeps that as the default text format while allowing operators to
select one JSON object per log record with ``LMAB_LOG_FORMAT=json``. Secret
looking values in messages and structured fields are redacted before output.

Environment:
    LMAB_LOG_FORMAT=text|json   (default: text)
    LMAB_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR|CRITICAL
    LMAB_DEBUG=0/1              (legacy gate for debug_print; default: 1)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

from . import constants

LOGGER_NAME = "lmarenabridge"
_HANDLER_MARKER = "_lmab_handler"

_SECRET_FIELD_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "auth_token",
    "access_token",
    "refresh_token",
    "recaptcha",
    "cf_clearance",
    "cookie",
    "password",
    "secret",
    "token",
)

_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{3,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SK_RE = re.compile(r"\bsk-(?:lmab-)?[A-Za-z0-9][A-Za-z0-9_-]{7,}\b")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"cf_clearance|arena-auth-prod-v1|password|secret|signature|sig)\b"
    r"(\s*[:=]\s*)([^\s,;&]+)"
)
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:signature|sig|token|key|secret|password)=)([^&\s]+)"
)


def _fingerprint(value: object) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "<redacted empty>"
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"<redacted sha256:{digest} len={len(text)}>"


def redact_text(value: object) -> str:
    """Redact common credential shapes from arbitrary log text."""
    text = "" if value is None else str(value)
    text = _JWT_RE.sub(lambda m: _fingerprint(m.group(0)), text)
    text = _BEARER_RE.sub(lambda m: "Bearer " + _fingerprint(m.group(0).split(None, 1)[-1]), text)
    text = _SK_RE.sub(lambda m: _fingerprint(m.group(0)), text)
    text = _ASSIGNMENT_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_fingerprint(m.group(3))}", text
    )
    text = _URL_SECRET_RE.sub(lambda m: f"{m.group(1)}{_fingerprint(m.group(2))}", text)
    return text


def _is_secret_field(name: object) -> bool:
    lowered = str(name).lower().replace("-", "_")
    return any(part in lowered for part in _SECRET_FIELD_PARTS)


def sanitize_fields(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-safe, redacted copy of structured log fields."""
    if _depth > 5:
        return "<truncated>"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_field(key_text):
                sanitized[key_text] = _fingerprint(item)
            else:
                sanitized[key_text] = sanitize_fields(item, _depth=_depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [sanitize_fields(item, _depth=_depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        return redact_text(value)[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(repr(value))[:500]


class JsonFormatter(logging.Formatter):
    """Format one redacted JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = str(event)
        fields = getattr(record, "fields", None)
        if fields:
            payload["fields"] = sanitize_fields(fields)
        if record.exc_info and record.exc_info[0] is not None:
            # Avoid full tracebacks/exception strings in JSON mode because
            # exception text can contain URLs, headers, or credentials.
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class RedactingTextFormatter(logging.Formatter):
    """Message-only text formatter that preserves legacy console output."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        message = redact_text(record.getMessage())
        event = getattr(record, "event", None)
        fields = getattr(record, "fields", None)
        suffix: list[str] = []
        if event:
            suffix.append(f"event={event}")
        if fields:
            suffix.append(
                "fields=" + json.dumps(sanitize_fields(fields), ensure_ascii=False, sort_keys=True)
            )
        if record.exc_info and record.exc_info[0] is not None:
            suffix.append(f"exception_type={record.exc_info[0].__name__}")
        return message + ((" | " + " ".join(suffix)) if suffix else "")


class _SafeStream:
    """Stream wrapper that cannot raise UnicodeEncodeError on legacy consoles."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, value: str) -> int:
        try:
            return self._stream.write(value)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, "encoding", None) or "utf-8"
            safe = value.encode(encoding, errors="backslashreplace").decode(
                encoding, errors="ignore"
            )
            return self._stream.write(safe)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _parse_level(value: Optional[object]) -> Optional[int]:
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = logging.getLevelName(value.strip().upper())
    return candidate if isinstance(candidate, int) else None


def _default_level() -> int:
    return logging.DEBUG if bool(getattr(constants, "DEBUG", True)) else logging.INFO


def configure_logging(
    *,
    level: Optional[object] = None,
    fmt: Optional[str] = None,
    stream: Optional[TextIO] = None,
    force: bool = False,
) -> logging.Logger:
    """Configure and return the bridge logger.

    Calling without ``force`` is idempotent. ``force=True`` replaces only
    handlers installed by this module, which is useful in tests and for
    runtime reconfiguration.
    """
    logger = logging.getLogger(LOGGER_NAME)
    existing = [h for h in logger.handlers if getattr(h, _HANDLER_MARKER, False)]
    if existing and not force:
        return logger
    for handler in existing:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    selected_level = _parse_level(level)
    if selected_level is None:
        selected_level = _parse_level(os.getenv("LMAB_LOG_LEVEL"))
    if selected_level is None:
        selected_level = _default_level()

    selected_format = (fmt or os.getenv("LMAB_LOG_FORMAT") or constants.LOG_FORMAT or "text")
    selected_format = str(selected_format).strip().lower()
    handler = logging.StreamHandler(stream if stream is not None else _SafeStream(sys.stdout))
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(JsonFormatter() if selected_format == "json" else RedactingTextFormatter())
    logger.handlers = [handler]
    logger.setLevel(selected_level)
    logger.propagate = False
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return the configured root bridge logger or one of its children."""
    root = configure_logging()
    if not name or name == LOGGER_NAME:
        return root
    if name.startswith(LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: object,
    **fields: Any,
) -> None:
    """Emit a structured event with redacted message and fields."""
    logger.log(
        level,
        redact_text(message),
        extra={"event": str(event), "fields": sanitize_fields(fields)},
    )
