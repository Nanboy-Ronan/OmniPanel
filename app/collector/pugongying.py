"""Pugongying (蒲公英) KOL/KOC collaboration platform export collector.

Downloads the collaboration report xlsx from 蒲公英 → 内容管理 → 带出,
and returns it unchanged for app/db/etl/pgy.parse_pgy_xlsx to consume.

── Auth flow ──────────────────────────────────────────────────────────
1. Login is at https://pgy.xiaohongshu.com/ — requires Xiaohongshu
   professional account credentials (same as XHS creator portal).
2. pgy.xiaohongshu.com is a separate subdomain from pro.xiaohongshu.com
   and creator.xiaohongshu.com. Whether CAS SSO sessions are shared
   between them is unverified — this module uses its own session files.
3. The export page is at:
   https://pgy.xiaohongshu.com/solar/post-trade/content-manage
   Export button text is "带出" (export/take-out).
"""
from __future__ import annotations

from pathlib import Path

from .browser import expect_download, looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, SessionExpiredError

PGY_HOME_URL = "https://pgy.xiaohongshu.com/"
PGY_LOGIN_URL = "https://pgy.xiaohongshu.com/"
PGY_DATA_URL = "https://pgy.xiaohongshu.com/solar/post-trade/content-manage"
PGY_EXPORT_BUTTON = 'button:has-text("带出")'

_LOGIN_CHECK_POLL_MS = 1000
_LOGIN_CHECK_MAX_MS = 15000


def _looks_expired(page) -> bool:
    return looks_like_login(page.url, visible_text(page))


def _goto_and_check_login(page, url: str) -> bool:
    page.goto(url, wait_until="domcontentloaded")
    waited = 0
    while waited < _LOGIN_CHECK_MAX_MS:
        page.wait_for_timeout(_LOGIN_CHECK_POLL_MS)
        waited += _LOGIN_CHECK_POLL_MS
        if not _looks_expired(page):
            return False
    return _looks_expired(page)


def verify_pugongying_session(storage_path: Path, *, headless: bool | None = None) -> bool:
    with open_context(storage_path, headless=headless) as page:
        if _goto_and_check_login(page, PGY_HOME_URL):
            return False
        return not _goto_and_check_login(page, PGY_DATA_URL)


def collect_pugongying(storage_path: Path, *, headless: bool | None = None) -> tuple[bytes, str]:
    with open_context(storage_path, headless=headless) as page:
        if _goto_and_check_login(page, PGY_HOME_URL):
            save_debug_artifacts(page, "pugongying_session_expired")
            raise SessionExpiredError(f"Pugongying session expired (redirected to {page.url!r})")

        if _goto_and_check_login(page, PGY_DATA_URL):
            save_debug_artifacts(page, "pugongying_session_expired")
            raise SessionExpiredError(f"Pugongying session expired (redirected to {page.url!r})")

        try:
            download = expect_download(page, lambda: page.click(PGY_EXPORT_BUTTON))
        except Exception as exc:
            save_debug_artifacts(page, "pugongying_download_timeout")
            raise DownloadTimeoutError(f"Pugongying export download did not complete: {exc}") from exc

        download_path = download.path()
        data = Path(download_path).read_bytes()
        filename = download.suggested_filename or "pugongying_export.xlsx"
        return data, filename
