"""Playwright browser/context helpers shared by every platform module.

Only app/collector/* may import playwright — keeps the dependency out of the
FastAPI backend and Streamlit UI processes entirely.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Iterator

from ..config import settings
from .paths import new_debug_prefix, prune_debug

_logger = logging.getLogger(__name__)

# Pin to a current desktop Chrome UA matching the Playwright-bundled Chromium
# major version — update alongside the `playwright` pin in requirements.txt.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1440, "height": 900}
LOCALE = "zh-CN"
TIMEZONE_ID = "Asia/Shanghai"

# URL/DOM markers that indicate a portal redirected us to a login/QR screen
# instead of the data page we asked for. Kept broad on purpose: a false
# positive here just means an extra debug screenshot, a false negative means
# a run silently succeeds with an empty export.
#
# "sso" was dropped 2026-07-15 after a live false positive: it matched the
# substring inside the HTML attribute `crossorigin="..."` (cro-SSO-rigin) on
# every single <script> tag — not an actual SSO redirect. Lesson: keep
# URL markers specific enough that they won't collide with ordinary HTML.
LOGIN_URL_MARKERS = ("login", "passport")
# Matched against *visible rendered text* (page.inner_text("body") — see
# visible_text() below), never raw page.content(). Checking raw HTML source
# was tried first and produced a false positive: "qrcode" matched scoped-CSS
# class names (.qrcode-box, .qrcode-modal) shipped in the bundle for a QR
# component that wasn't even displayed on that page. Visible-text-only
# checking avoids matching anything in <style>/<script> that isn't on screen.
LOGIN_DOM_MARKERS = (
    "二维码", "扫码登录", "扫码登陆",
    "短信登录", "发送验证码", "验证码登录",
)


def visible_text(page) -> str:
    """Return only the rendered, on-screen text of the page body.

    Deliberately not page.content(): raw HTML source includes bundled
    <style>/<script> content, where almost any keyword can appear as a CSS
    class name or JS string literal for a component that isn't currently
    displayed — see the LOGIN_DOM_MARKERS docstring above for a real example
    of that producing a false positive.
    """
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def looks_like_login(url: str, text: str) -> bool:
    """Pure heuristic: does this url/visible-text look like a login (or QR) screen?

    `text` should be visible rendered text (see visible_text()), not raw
    HTML source. No browser involved here — kept side-effect-free so it's
    cheap to unit test against fixture strings without ever launching
    Chromium.
    """
    u = (url or "").lower()
    if any(marker in u for marker in LOGIN_URL_MARKERS):
        return True
    t = text or ""
    return any(marker in t for marker in LOGIN_DOM_MARKERS)


def save_debug_artifacts(page, tag: str) -> None:
    """Best-effort screenshot + HTML dump on failure. Never raises — a broken
    debug dump must not mask the original collector error."""
    try:
        prefix = new_debug_prefix(tag)
        page.screenshot(path=str(prefix.with_suffix(".png")), full_page=True)
        prefix.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        prune_debug()
    except Exception as exc:
        _logger.warning("save_debug_artifacts_failed tag=%r: %s", tag, exc)


@contextlib.contextmanager
def open_context(storage_state_path: Path, headless: bool | None = None) -> Iterator:
    """Launch Chromium with a saved storage_state and yield a ready `page`.

    Caller is responsible for closing over portal-specific navigation; this
    only sets up the browser/context/page triple and tears it down cleanly.
    """
    from playwright.sync_api import sync_playwright

    headless = settings.collector_headless if headless is None else headless

    with sync_playwright() as pw:
        # No --disable-blink-features=AutomationControlled: confirmed live
        # 2026-07-15 that this flag itself gets XHS's pro.xiaohongshu.com to
        # treat an otherwise-valid session as invalid and force it back to
        # /login — likely because the flag's absence of the usual
        # navigator.webdriver signal is itself an anomaly no genuine user
        # browser would have. Isolated by A/B testing the same saved session
        # with/without each arg; --disable-dev-shm-usage alone was fine.
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(
                storage_state=str(storage_state_path),
                accept_downloads=True,
                viewport=VIEWPORT,
                locale=LOCALE,
                timezone_id=TIMEZONE_ID,
                user_agent=USER_AGENT,
            )
            context.set_default_navigation_timeout(settings.collector_nav_timeout_seconds * 1000)
            context.set_default_timeout(settings.collector_nav_timeout_seconds * 1000)
            try:
                page = context.new_page()
                yield page
            finally:
                context.storage_state(path=str(storage_state_path))
                context.close()
        finally:
            browser.close()


@contextlib.contextmanager
def fresh_context(headless: bool | None = None) -> Iterator:
    """Launch Chromium with a blank (unauthenticated) context — used only by
    `bootstrap-login` to run a fresh QR-login flow."""
    from playwright.sync_api import sync_playwright

    headless = False if headless is None else headless  # bootstrap always needs a human to see the QR

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                accept_downloads=True,
                viewport=VIEWPORT,
                locale=LOCALE,
                timezone_id=TIMEZONE_ID,
                user_agent=USER_AGENT,
            )
            try:
                page = context.new_page()
                yield page, context
            finally:
                context.close()
        finally:
            browser.close()


def expect_download(page, trigger, *, timeout_seconds: int | None = None):
    """Click/act via `trigger()` and return the Playwright Download object.

    Raises playwright.sync_api.TimeoutError if nothing downloads in time —
    callers translate that into DownloadTimeoutError with portal context.
    """
    timeout_ms = (timeout_seconds or settings.collector_download_timeout_seconds) * 1000
    with page.expect_download(timeout=timeout_ms) as dl_info:
        trigger()
    return dl_info.value
