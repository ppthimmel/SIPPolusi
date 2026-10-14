"""Container entry point: validate configuration, then start uvicorn.

    python serve.py           # check configuration, then serve on $PORT
    python serve.py --check   # check configuration only; exit 0 if valid, 2 if not

Invalid configuration stops the process before the port is bound, so a
Railway deployment fails its health check with the reason in the deploy log
instead of serving a half-configured backend.
"""
from __future__ import annotations

import argparse
import logging
import sys

from log_config import configure_logging
from settings import ConfigError, load_settings

log = logging.getLogger("backend")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serve.py", description="SIPPolusi backend")
    parser.add_argument("--check", action="store_true", help="validate configuration and exit")
    args = parser.parse_args(argv)

    configure_logging()
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("invalid configuration, not starting:\n%s", exc)
        return 2
    log.info("configuration ok: %s", settings.summary())
    if settings.database_url is None:
        log.warning("DATABASE_URL is not set; /health/ready will report the database as not configured")
    if args.check:
        return 0

    import uvicorn

    # Empty host = all interfaces: asyncio opens separate IPv4 and IPv6 sockets,
    # so both Railway's public edge (IPv4) and private network (IPv6) can connect.
    # log_config=None keeps the handler installed by configure_logging().
    uvicorn.run("main:app", host="", port=settings.port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
