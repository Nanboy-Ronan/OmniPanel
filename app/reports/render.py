"""Renders the weekly report context (app/reports/weekly_media.py) + optional
narrative (app/reports/narrative.py) into a self-contained HTML document."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from jinja2.runtime import Undefined

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


def _is_invalid(value: Any) -> bool:
    return value is None or isinstance(value, Undefined)


def _fmt_num(value: Any) -> str:
    """For values where a decimal is meaningful (durations, rates)."""
    if _is_invalid(value):
        return "—"
    try:
        if isinstance(value, float):
            return f"{value:,.2f}"
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return "—"


def _fmt_int(value: Any) -> str:
    """For counts/totals, which should never show a decimal — a float here
    (e.g. an averaged view count) is rounded, not truncated to two decimals."""
    if _is_invalid(value):
        return "—"
    try:
        return f"{round(float(value)):,}"
    except (ValueError, TypeError):
        return "—"


def _fmt_pct(value: Any) -> str:
    if _is_invalid(value):
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (ValueError, TypeError):
        return "—"


def _fmt_date(value: Any) -> str:
    if _is_invalid(value):
        return "—"
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _fmt_datetime(value: Any) -> str:
    """Timestamp to the minute (no seconds/microseconds/offset); aware values
    are shown in their own zone — the report passes APP_TIMEZONE time."""
    if _is_invalid(value):
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _sparkline(deltas: list[tuple[Any, int]], width: int = 160, height: int = 32) -> str:
    """Tiny inline SVG sparkline for a per-post daily-reads series — no
    charting library. Per the stat-tile spec: the trend line stays in the
    de-emphasis (muted) tone, with only the current/last point picked out in
    the accent color — the line is context for the number beside it, not a
    second thing competing for attention."""
    values = [v for _, v in deltas]
    if not values or max(values) == 0:
        return ""
    max_v = max(values)
    n = len(values)
    pad = 3
    step = (width - 2 * pad) / max(1, n - 1)

    def _xy(i: int, v: int) -> tuple[float, float]:
        x = pad + i * step
        y = height - pad - (v / max_v) * (height - 2 * pad)
        return x, y

    points = " ".join(f"{x:.1f},{y:.1f}" for i, v in enumerate(values) for x, y in [_xy(i, v)])
    last_x, last_y = _xy(n - 1, values[-1])
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'class="sparkline" aria-hidden="true">'
        f'<polyline points="{points}" fill="none" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" style="stroke:var(--ink-muted)" />'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" style="fill:var(--accent)" />'
        f"</svg>"
    )


_env.filters["fmt_num"] = _fmt_num
_env.filters["fmt_int"] = _fmt_int
_env.filters["fmt_pct"] = _fmt_pct
_env.filters["fmt_date"] = _fmt_date
_env.filters["fmt_datetime"] = _fmt_datetime
_env.filters["sparkline"] = _sparkline


def render_html(context: dict[str, Any], narrative: str | None) -> str:
    template = _env.get_template("weekly_media.html.j2")
    return template.render(context=context, narrative=narrative)
