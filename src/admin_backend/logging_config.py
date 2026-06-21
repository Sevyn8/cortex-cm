"""Structured stdout JSON logging configuration.

GCP Cloud Logging picks up stdout JSON automatically; no agent needed.
Per D-20, the v0 logging stack is python-json-logger (stdout) and
prometheus-fastapi-instrumentator (metrics; lands at Step 7.2).

The PyPI package is `python-json-logger` (with hyphens). Import path
is `from pythonjsonlogger import jsonlogger` (no hyphens or
underscores). Common gotcha; pinned in pyproject as
`python-json-logger>=2.0,<4.0` per D-26-style version-stability
discipline (v3.x still ships the `jsonlogger` import path; v4.x
relocated it).
"""
import logging
import sys

from pythonjsonlogger import jsonlogger


def configure_logging(level: str = "INFO") -> None:
    """Configure stdout JSON logging.

    Replaces the root logger's handlers with a single JsonFormatter
    handler. Idempotent: safe to call multiple times (e.g. from
    lifespan + tests).
    """
    handler = logging.StreamHandler(sys.stdout)
    # JsonFormatter is exported by jsonlogger in v2.x and re-exported via
    # a compatibility shim in v3.x; the v3 shim doesn't carry the
    # __all__ marker, so mypy cannot see the attribute.
    formatter = jsonlogger.JsonFormatter(  # type: ignore[attr-defined]
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
