"""Process-wide logging: one line per event on stdout, credentials masked.

Railway tags everything written to stderr as an error, so all records go to
stdout. On Railway (or with LOG_FORMAT=json) each record is a JSON object with
a ``level`` field, which Railway's log explorer uses for filtering; locally it
is a plain text line. Secrets are masked in the final formatted text, which
also covers exception tracebacks (e.g. a DSN echoed back by a driver error).

Kept identical in src/backend and src/data_worker because each service is
built from its own Docker context.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sys
from collections.abc import Iterable, Mapping

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
    def __init__(self, *, as_json: bool, secrets: Iterable[str] = ()):
        super().__init__()
        self.as_json = as_json
        self.secrets = list(secrets)

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"
        message = redact(message, self.secrets)
        moment = dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds")
        if self.as_json:
            return json.dumps(
                {
                    "time": moment,
                    "level": _RAILWAY_LEVEL.get(record.levelname, record.levelname.lower()),
                    "logger": record.name,
                    "message": message,
                },
                ensure_ascii=False,
            )
        return f"{moment} {record.levelname:<7} {record.name}: {message}"


def configure_logging(environ: Mapping[str, str] | None = None, *, level: str | None = None) -> None:
    """Install the stdout handler on the root logger, replacing any other.

    LOG_LEVEL (default INFO, overridden by ``level``) and LOG_FORMAT
    (json|text; default json on Railway, text elsewhere) are read here.
    Invalid values fall back to the defaults; the settings check reports them.
    """
    env = os.environ if environ is None else environ
    level = (level or env.get("LOG_LEVEL", "INFO")).upper()
    log_format = env.get("LOG_FORMAT", "").lower() or ("json" if env.get("RAILWAY_ENVIRONMENT_NAME") else "text")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter(as_json=log_format == "json", secrets=secret_values(env)))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level if level in _LEVELS else "INFO")
