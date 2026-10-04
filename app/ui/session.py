"""Session-state bootstrap: identity defaults and the shareable filter seeds.

Split out of dashboard.py's script body. The seeded-filter list is the reason
widgets for these keys must not also pass `value=`/`index=` — doing both makes
Streamlit print a warning banner above the filter bar — so the list is exported
rather than left for a test to parse back out of the source.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import streamlit as st

from app.ui import registry
from app.ui.url_state import parse_shared_filters

PLATFORM_CHOICES = {"全部", "youzan", "jd", "tmall"}
ANALYSIS_MODES = {"概览", "新老客户"}

#: Filters seeded here and therefore owned by session state, not by the widget.
#: `(key, kind)` — kind drives how a matching query parameter is coerced.
SEEDED_FILTERS: tuple[tuple[str, str], ...] = (
    ("analysis_start", "date"),
    ("analysis_end", "date"),
    ("analysis_platform", "platform"),
    ("analysis_mode", "mode"),
    ("cust_start", "date"),
    ("cust_end", "date"),
    ("cust_min_orders", "int"),
    ("cust_platform", "platform"),
)

SEEDED_FILTER_KEYS = frozenset(key for key, _ in SEEDED_FILTERS)


def _default_for(kind: str, today: date) -> Any:
    return {
        "date": today,
        "platform": "全部",
        "mode": "概览",
        "int": 1,
    }[kind]


def _coerce(kind: str, raw: str) -> Any | None:
    """Turn a query-parameter string into a value, or None if it is not valid."""
    if kind == "date":
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    if kind == "platform":
        return raw if raw in PLATFORM_CHOICES else None
    if kind == "mode":
        return raw if raw in ANALYSIS_MODES else None
    return None


def seed_filters(state, params, today: date | None = None) -> None:
    """Give every shareable filter a value, preferring a valid query parameter."""
    today = today or date.today()
    thirty_days_ago = today - timedelta(days=30)

    for key, kind in SEEDED_FILTERS:
        if key in state:
            continue
        default = thirty_days_ago if key.endswith("_start") else _default_for(kind, today)
        raw = params.get(key)
        if raw:
            coerced = _coerce(kind, raw)
            if coerced is not None:
                default = coerced
        state[key] = default


def bootstrap_state(state, params) -> None:
    """Populate identity and filter defaults for a fresh session."""
    state.setdefault("token", None)
    state.setdefault("page", params.get("page", registry.default_page("")))
    state.setdefault("_active_section", registry.ECOMMERCE)
    state.setdefault("is_admin", False)
    state.setdefault("display_name", "")
    state.setdefault("user_role", "")

    seed_filters(state, params)

    if not state.get("_url_filters_loaded"):
        state.update(parse_shared_filters(state["page"], params))
        state["_url_filters_loaded"] = True


def attach_client(state, client_factory):
    """Reuse the session's API client, keeping its token in step."""
    if "client" not in state:
        state["client"] = client_factory(token=state["token"])
    else:
        state["client"].token = state["token"]
    return state["client"]


def refresh_identity(state, client) -> None:
    """Fill in display name and role once, from /me."""
    if not state["token"]:
        return
    if not state.get("user_role"):
        try:
            response = client.me()
            if response.status_code == 200:
                me = response.json()
                state["display_name"] = me.get("display_name", "")
                state["user_role"] = me.get("role", "viewer")
        except Exception:
            pass
    state["is_admin"] = state.get("user_role") == "admin"


def session_expires_in(token: str) -> float | None:
    """Seconds until the JWT expires, or None when it cannot be read."""
    from app.ui._helpers import _decode_jwt_payload
    import time

    if not token:
        return None
    exp = _decode_jwt_payload(token).get("exp", 0)
    if not exp:
        return None
    return exp - time.time()


__all__ = [
    "SEEDED_FILTERS",
    "SEEDED_FILTER_KEYS",
    "attach_client",
    "bootstrap_state",
    "refresh_identity",
    "seed_filters",
    "session_expires_in",
    "st",
]
