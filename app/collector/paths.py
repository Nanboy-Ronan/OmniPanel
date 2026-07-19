"""Filesystem layout under settings.collector_dir.

    {collector_dir}/
    ├── sessions/    xhs_{account_id}.json, zhihu.json  (chmod 700 dir, 0600 files)
    ├── downloads/   per-run scratch, safe to clear any time
    └── debug/       failure screenshot+HTML pairs, pruned to collector_debug_keep
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from ..config import settings


def _base_dir() -> Path:
    p = Path(settings.collector_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sessions_dir() -> Path:
    d = _base_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass  # best-effort on filesystems that don't support chmod (e.g. some CI runners)
    return d


def downloads_dir() -> Path:
    d = _base_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def debug_dir() -> Path:
    d = _base_dir() / "debug"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_path(platform: str, account_id: int | None) -> Path:
    """Return the storage_state.json path for a given target.

    XHS sessions are per-account (``xhs_{account_id}.json``); Zhihu has a
    single login shared by both content types (``zhihu.json``).
    """
    if platform == "xhs":
        if account_id is None:
            raise ValueError("account_id is required for platform='xhs'")
        return sessions_dir() / f"xhs_{account_id}.json"
    if platform == "zhihu":
        return sessions_dir() / "zhihu.json"
    raise ValueError(f"Unknown platform: {platform!r}")


def prune_debug(keep: int | None = None) -> None:
    """Keep only the most recent `keep` debug artifact pairs (by mtime)."""
    keep = keep if keep is not None else settings.collector_debug_keep
    d = debug_dir()
    files = sorted(d.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    # Each failure writes a .png + .html pair sharing a timestamp prefix, so
    # 2*keep files preserves `keep` most-recent pairs even if one half is missing.
    for f in files[2 * keep:]:
        try:
            f.unlink()
        except OSError:
            pass


def new_debug_prefix(tag: str) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return debug_dir() / f"{ts}_{tag}"
