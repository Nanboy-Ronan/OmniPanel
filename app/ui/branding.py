"""Static brand assets shared by the sidebar and the login view."""
from __future__ import annotations

import base64
from pathlib import Path

ICON_PATH = Path(__file__).parent / "static" / "omnipanel.svg"
ICON_DATA_URL = "data:image/svg+xml;base64," + base64.b64encode(ICON_PATH.read_bytes()).decode("ascii")

SIDEBAR_BRAND_HTML = (
    "<div class='dashboard-logo'>"
    f"<img class='dashboard-logo-mark' src='{ICON_DATA_URL}' alt=''>"
    "<div><div class='dashboard-logo-text'>OmniPanel</div>"
    "<div class='dashboard-logo-sub'>数据分析平台</div></div>"
    "</div>"
)

CSS = "<style>" + (Path(__file__).parent / "static" / "dashboard.css").read_text(encoding="utf-8") + "</style>"
