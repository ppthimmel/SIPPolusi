"""Process-wide logging: one line per event on stdout, credentials masked.

Railway tags everything written to stderr as an error, so all records go to
stdout. On Railway (or with LOG_FORMAT=json) each record is a JSON object with
a ``level`` field, which Railway's log explorer uses for filtering; locally it
is a plain text line. Secrets are masked in the final formatted text, which
also covers exception tracebacks (e.g. a DSN echoed back by a driver error).

Correlation (TI-DO-08): code that handles one request or one job runs inside
``correlation(id)``; every record logged meanwhile, in any module, carries
that ``correlation_id``. ``log_event`` writes one structured event: in JSON
its fields are top-level keys (filterable in Railway as ``@status:failed``),
in text they read as ``event key=value ...``. The log schema is documented
in docs/devops/observability-TI-DO-08.md.

Kept identical in src/backend and src/data_worker because each service is
built from its own Docker context.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Iterable, Iterator, Mapping

MASK = "***"

# scheme://user:password@host -> scheme://user:***@host
_URL_CREDENTIALS = re.compile(r"(?P<prefix>\b[a-z][a-z0-9+.-]*://[^:/?#@\s]*):[^@/\s]+@", re.IGNORECASE)
# password=..., token: ..., api_key=... (libpq key=value DSNs, query strings)
_KEY_VALUE_SECRET = re.compile(
    r"(?P<key>\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[=:]\s*)"
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s&,;'\"]+)",
    re.IGNORECASE,
)
_SECRET_VAR_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|_KEY|DATABASE_URL|STORE_URI|DSN", re.IGNORECASE)
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
# Railway's log explorer understands debug, info, warn and error.
_RAILWAY_LEVEL = {"WARNING": "warn", "CRITICAL": "error"}
# An incoming correlation ID (X-Request-ID) is only adopted if it looks like an
# ID; anything else could inject text into the log and is replaced instead.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# Keys of the JSON record itself; event fields never overwrite them.
_RESERVED = frozenset({"time", "level", "logger", "message", "service", "correlation_id", "event"})

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


def safe_correlation_id(value: str | None) -> str | None:
    """``value`` if it is safe to log and propagate as a correlation ID, else None."""
    return value if value and _SAFE_ID.match(value) else None


def new_correlation_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4()}"


def current_correlation_id() -> str | None:
    return _correlation_id.get()


@contextlib.contextmanager
def correlation(correlation_id: str) -> Iterator[str]:
    """Tag every record logged inside the block (same task or thread) with ``correlation_id``."""
    token = _correlation_id.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _correlation_id.reset(token)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, /, **fields: object) -> None:
    """Log one structured event. Fields that are None are left out; secret-named ones are masked."""
    fields = {
        key: MASK if _SECRET_VAR_NAME.search(key) else value for key, value in fields.items() if value is not None
    }
    message = " ".join([event, *(f"{key}={value}" for key, value in fields.items())])
    logger.log(level, "%s", message, extra={"event": event, "event_fields": fields})


def secret_values(environ: Mapping[str, str]) -> list[str]:
    """Values of secret-looking variables, longest first, to mask verbatim."""
    values = {value for name, value in environ.items() if _SECRET_VAR_NAME.search(name) and len(value) >= 8}
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    text = _URL_CREDENTIALS.sub(lambda m: f"{m['prefix']}:{MASK}@", text)
    text = _KEY_VALUE_SECRET.sub(lambda m: f"{m['key']}{MASK}", text)
    for value in secrets:
        text = text.replace(value, MASK)
    return text


class RedactingFormatter(logging.Formatter):
    def __init__(self, *, as_json: bool, secrets: Iterable[str] = (), service: str | None = None):
        super().__init__()
        self.as_json = as_json
        self.secrets = list(secrets)
        self.service = service

    def _field(self, key: str, value: object) -> object:
        if _SECRET_VAR_NAME.search(key):
            return MASK
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return redact(str(value), self.secrets)

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"
        message = redact(message, self.secrets)
        moment = dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds")
        correlation_id = _correlation_id.get()
        if self.as_json:
            payload: dict[str, object] = {
                "time": moment,
                "level": _RAILWAY_LEVEL.get(record.levelname, record.levelname.lower()),
                "logger": record.name,
                "message": message,
            }
            if self.service:
                payload["service"] = self.service
            if correlation_id:
                payload["correlation_id"] = correlation_id
            event = getattr(record, "event", None)
            if event:
                payload["event"] = event
                for key, value in getattr(record, "event_fields", {}).items():
                    if key not in _RESERVED:
                        payload[key] = self._field(key, value)
            return json.dumps(payload, ensure_ascii=False, default=str)
        context = f" [{correlation_id}]" if correlation_id else ""
        return f"{moment} {record.levelname:<7} {record.name}{context}: {message}"


def configure_logging(
    environ: Mapping[str, str] | None = None, *, level: str | None = None, service: str | None = None
) -> None:
    """Install the stdout handler on the root logger, replacing any other.

    LOG_LEVEL (default INFO, overridden by ``level``) and LOG_FORMAT
    (json|text; default json on Railway, text elsewhere) are read here.
    Invalid values fall back to the defaults; the settings check reports them.
    ``service`` names the emitting service in JSON records (default:
    RAILWAY_SERVICE_NAME, if set).
    """
    env = os.environ if environ is None else environ
    level = (level or env.get("LOG_LEVEL", "INFO")).upper()
    log_format = env.get("LOG_FORMAT", "").lower() or ("json" if env.get("RAILWAY_ENVIRONMENT_NAME") else "text")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter(
            as_json=log_format == "json",
            secrets=secret_values(env),
            service=service or env.get("RAILWAY_SERVICE_NAME") or None,
        )
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level if level in _LEVELS else "INFO")
