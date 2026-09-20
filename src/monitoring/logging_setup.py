"""Structured logging with mandatory secret redaction.

Rule: nothing that looks like a private key, seed phrase, API secret,
Telegram token or Authorization header may ever reach a log sink, a
database row or an exception message. Redaction happens in the formatter,
so it applies to every handler at once.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import re
import sys
from pathlib import Path

# Patterns matched against the *message* before it is emitted.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"\b(0x)?[0-9a-fA-F]{64}\b")),
    ("mnemonic", re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b")),
    # Telegram bot tokens are "<bot_id>:<35 char secret>". The secret is
    # URL-safe base64-ish, so match a wide charset and a range rather than an
    # exact length - a longer/shorter real token must still be redacted.
    ("telegram_bot_token", re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\b(authorization|bearer)\s*[:=]?\s*[A-Za-z0-9._\-]{8,}")),
    ("poly_api_key", re.compile(r"(?i)\b(api[_-]?key|api[_-]?secret|passphrase)\b\s*[:=]\s*\S+")),
)
_REDACTION = "[REDACTED]"

# Keys dropped from structured payloads.
DEFAULT_REDACT_KEYS = frozenset(
    {
        "private_key", "privatekey", "pk", "secret", "api_secret", "apisecret",
        "api_key", "apikey", "passphrase", "bot_token", "seed", "mnemonic",
        "authorization", "password", "token",
    }
)


def redact_text(text: str) -> str:
    """Return *text* with anything resembling a credential replaced."""
    if not text:
        return text
    for _name, pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTION, text)
    return text


def redact_mapping(payload: dict, redact_keys: frozenset[str] = DEFAULT_REDACT_KEYS) -> dict:
    """Recursively drop/redact sensitive keys from a structured payload."""
    out: dict = {}
    for key, value in payload.items():
        if key.lower() in redact_keys:
            out[key] = _REDACTION
        elif isinstance(value, dict):
            out[key] = redact_mapping(value, redact_keys)
        elif isinstance(value, list):
            out[key] = [
                redact_mapping(v, redact_keys) if isinstance(v, dict) else redact_text(str(v))
                for v in value
            ]
        elif isinstance(value, str):
            out[key] = redact_text(value)
        else:
            out[key] = value
    return out


class RedactingFilter(logging.Filter):
    """Applies :func:`redact_text` to every record before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = redact_text(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact_mapping(record.args)
                else:
                    record.args = tuple(redact_text(str(a)) for a in record.args)
        except Exception:  # pragma: no cover - never let logging break the app
            pass
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for extra_key in ("event", "signal_id", "market_id", "mode", "component"):
            if hasattr(record, extra_key):
                payload[extra_key] = getattr(record, extra_key)
        return json.dumps(redact_mapping(payload), ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        line = f"{stamp} {record.levelname:<8} {record.name:<34} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _StdoutHandler(logging.StreamHandler):
    """StreamHandler that writes to whatever ``sys.stdout`` is *now*.

    Holding a reference to the stream captured at construction time breaks
    output capture (test harnesses, log rotation, re-exec under a supervisor),
    so the stream is resolved per emit instead.
    """

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:  # pragma: no cover - logging sets this in __init__
        self._initial_stream = value


def setup_logging(
    level: str = "INFO",
    log_dir: str | Path | None = "logs",
    as_json: bool = False,
    component: str = "app",
    force: bool = False,
) -> logging.Logger:
    """Configure the root logger and return this component's logger.

    Idempotent by default (a process configures logging once); pass ``force=True``
    to tear the configuration down and rebuild it - used by tests and by
    embedders that need to change the level or sink at runtime.
    """
    root = logging.getLogger()
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover - defensive
                pass
        root._fmt_configured = False  # type: ignore[attr-defined]
    if getattr(root, "_fmt_configured", False):
        return logging.getLogger(component)

    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter: logging.Formatter = JsonFormatter() if as_json else TextFormatter()
    filter_ = RedactingFilter()

    stream = _StdoutHandler()
    stream.setFormatter(formatter)
    stream.addFilter(filter_)
    root.addHandler(stream)

    if log_dir:
        try:
            path = Path(log_dir)
            path.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                path / "football_trader.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(filter_)
            root.addHandler(file_handler)
        except OSError as exc:  # pragma: no cover - read-only fs
            root.warning("could not open log file: %s", exc)

    root._fmt_configured = True  # type: ignore[attr-defined]
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger(component).debug("logging configured level=%s json=%s", level, as_json)
    return logging.getLogger(component)


def log_event(logger: logging.Logger, event: str, message: str, **fields) -> None:
    """Emit a structured, redacted event line."""
    safe = redact_mapping(fields)
    suffix = " ".join(f"{k}={v}" for k, v in safe.items())
    logger.info("[%s] %s %s", event, message, suffix, extra={"event": event})
