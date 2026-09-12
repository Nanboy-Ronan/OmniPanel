"""Pugongying (蒲公英) KOL/KOC collaboration platform export collector.

Downloads the collaboration report xlsx from 蒲公英 → 笔记合作数据 → 导出,
and returns it unchanged for app/db/etl/pgy.parse_pgy_xlsx to consume.

── Auth flow ──────────────────────────────────────────────────────────
1. Login is at https://pgy.xiaohongshu.com/ — requires Xiaohongshu
   professional account credentials (same as XHS creator portal), via its
   own phone+SMS flow — no SSO with an existing pro.xiaohongshu.com/XHS
   session (confirmed live: a valid XHS session does not carry over here).
2. pgy.xiaohongshu.com is a separate subdomain from pro.xiaohongshu.com
   and creator.xiaohongshu.com. Whether CAS SSO sessions are shared
   between them is unverified — this module uses its own session files.
3. After successful phone+SMS auth, login lands on an intermediate
   "选择身份" page (URL contains "role-select") listing every sub-account
   the phone number has access to, each with its own "以子账号身份进入"
   button — found live re-running bootstrap-login for this platform. It is
   NOT a login page (looks_like_login correctly returns False for it) and
   NOT a completed login either — exactly the same shape of bug as XHS's
   select-account picker (see xhs.py docstring): the first bootstrap-login
   attempt against this page declared success and saved the session the
   moment it landed here (since a role-select page doesn't "look like" a
   login page), producing a session that passed verify-session but then
   timed out on the real export button during collect, because the
   account/role was never actually selected. bootstrap-login's poll loop
   now waits through this page like XHS's select-account, and
   _looks_expired() below treats a saved session that still redirects back
   to role-select as expired rather than valid — see
   PGY_ROLE_SELECT_URL_MARKER.
4. The export page is at:
   https://pgy.xiaohongshu.com/solar/post-trade/content-manage
   Export button text is "导出" — a single click starts the xlsx download
   directly, no confirm dialog or async export-then-poll step. Verified
   live against a real account: rows parsed cleanly by
   app/db/etl/pgy.parse_pgy_xlsx.

Not yet covered: unlike XHS (see XHS_WRONG_ACCOUNT_MARKER), there is no
known/verified signal here for "landed on the wrong sub-account" once past
role-select — the role-select step is human-supervised during
bootstrap-login (the operator clicks the button themselves, same as XHS's
account picker), so the risk is lower, but a wrong-account bug analogous to
XHS's is still structurally possible and would currently go undetected.
"""
from __future__ import annotations

from pathlib import Path

from .browser import expect_download, looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, SessionExpiredError

PGY_HOME_URL = "https://pgy.xiaohongshu.com/"
PGY_LOGIN_URL = "https://pgy.xiaohongshu.com/"
PGY_DATA_URL = "https://pgy.xiaohongshu.com/solar/post-trade/content-manage"
PGY_EXPORT_BUTTON = 'button:has-text("导出")'

# See module docstring point 3. Same shape as XHS_SELECT_ACCOUNT_URL_MARKER:
# not a login page, not a completed login either. A *saved* session that
# still redirects here means role/sub-account was never selected — that
# session can never succeed no matter how long _goto_and_check_login polls,
# so folding this into _looks_expired (rather than treating it as a
# transient state to wait out) is correct: it will keep reporting expired,
# which is also the correct remedy (re-run bootstrap-login).
PGY_ROLE_SELECT_URL_MARKER = "role-select"
PGY_ENTER_SUBACCOUNT_BUTTON_TEXT = "以子账号身份进入"

_LOGIN_CHECK_POLL_MS = 1000
_LOGIN_CHECK_MAX_MS = 15000


def _looks_expired(page) -> bool:
    return (
        PGY_ROLE_SELECT_URL_MARKER in page.url
        or looks_like_login(page.url, visible_text(page))
    )


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
