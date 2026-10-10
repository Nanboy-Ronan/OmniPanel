"""One-time root logging setup for the API and the collector CLI.

Nothing in the app configured the root logger, so under uvicorn every
``logging.getLogger("app.…").info(...)`` was dropped: uvicorn only attaches
handlers to its own ``uvicorn*`` loggers (with ``propagate=False``) and the
root logger falls back to Python's last-resort WARNING-only handler.

``configure_logging()`` attaches a single stderr handler to the root logger
(journald picks it up) at ``LOG_LEVEL`` (default INFO). It is idempotent and
leaves an already-configured root logger alone, so it never duplicates
handlers that uvicorn, pytest or an operator's own config installed, and
uvicorn's loggers keep their own handlers untouched.
"""
from __future__ import annotations

import logging
import os

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_MARK = "_rpa_root_handler"


def _resolve_level(raw: str | None) -> tuple[int, bool]:
    name = (raw or "INFO").strip().upper()
    level = logging.getLevelName(name)
    if isinstance(level, int):
        return level, True
    return logging.INFO, False


def configure_logging(level_name: str | None = None) -> None:
    root = logging.getLogger()
    if any(getattr(h, _HANDLER_MARK, False) for h in root.handlers):
        return
    level, valid = _resolve_level(level_name if level_name is not None else os.getenv("LOG_LEVEL"))
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)
    root.setLevel(level)
    if not valid:
        logging.getLogger(__name__).warning("Unknown LOG_LEVEL %r; using INFO", level_name or os.getenv("LOG_LEVEL"))
