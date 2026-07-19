"""Xiaohongshu (小红书) creator-portal export collector.

Downloads the same xlsx a human would get from 创作者中心 → 内容分析 → 导出,
and returns it unchanged for app/db/etl/xhs.parse_xhs_xlsx to consume
(banner row on line 0 intact — do not touch the bytes).

── Auth flow (fully verified live 2026-07-15) ────────────────────────────
1. Login is at https://pro.xiaohongshu.com/login via 短信登录 (phone number
   + SMS code — NOT a QR scan).
2. If the phone number is linked to more than one XHS professional account
   (common — this account's owner has two), login lands on an intermediate
   account-picker page (URL contains "select-account") before reaching a
   real dashboard. It is not a login page and not a completed login either;
   bootstrap-login's poll loop waits through it rather than stopping there
   (see XHS_SELECT_ACCOUNT_URL_MARKER / cli.py).
3. The actual data lives on a *different* subdomain, creator.xiaohongshu.com,
   authenticated via a CAS SSO handoff (customer.xiaohongshu.com/api/cas/...)
   — NOT simple shared-cookie SSO. A cold hit to a creator.xiaohongshu.com
   page transiently 401s and shows a "login" URL for a few seconds while a
   *silent* CAS service-ticket exchange runs in the background; the SPA then
   redirects itself back to the real page once the ticket lands (observed
   ~6s end to end). Treating that transient login URL as a real session
   expiry — which the first version of this module did — is a bug: it
   never gives the silent handoff a chance to finish. _goto_and_check_login()
   below only declares SessionExpiredError if the login state is *still*
   there after the full wait budget, not on first sight.
4. Two distinct exports exist on creator.xiaohongshu.com (per the account
   owner):
     /statistics/account/v2    — 数据概览: account-level aggregate traffic
                                  over a date range. NOT what this module
                                  collects — XhsPost is per-note, not
                                  account-level. Kept as a note for a
                                  possible future feature.
     /statistics/data-analysis — 内容分析: per-note (per-post) metrics,
                                  matches XhsPost/parse_xhs_xlsx exactly.
                                  This is XHS_DATA_URL below.
5. Export button text is exactly "导出数据"; clicking it starts the file
   download directly — there is no confirm dialog/second click.

One more real bug found and fixed along the way: launching Chromium with
``--disable-blink-features=AutomationControlled`` (browser.py's original
open_context()) got XHS's risk control to treat an otherwise-valid session
as invalid and force it back to login — no genuine user browser ever has
that flag, so its *presence* was itself the tell. See browser.py.
"""
from __future__ import annotations

from pathlib import Path

from .browser import expect_download, looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, SessionExpiredError

XHS_PRO_HOME_URL = "https://pro.xiaohongshu.com/"
XHS_LOGIN_URL = "https://pro.xiaohongshu.com/login"
XHS_ACCOUNT_OVERVIEW_URL = "https://creator.xiaohongshu.com/statistics/account/v2"  # not collected — see docstring
XHS_DATA_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
XHS_EXPORT_BUTTON = 'button:has-text("导出数据")'

# One phone number can be linked to multiple XHS professional accounts (found
# 2026-07-15: this account's owner has two separate professional accounts). After
# SMS verification, login lands on an intermediate account-picker page at this
# URL before reaching a real dashboard. It is NOT a login page (looks_like_login
# correctly returns False for it) but it is also NOT a completed login — the
# resulting session is only valid once a specific account has been clicked.
# bootstrap-login's poll loop must wait through this page, not stop at it.
XHS_SELECT_ACCOUNT_URL_MARKER = "select-account"

_LOGIN_CHECK_POLL_MS = 1000
# XHS's cross-domain auth is CAS-based (customer.xiaohongshu.com/api/cas/...):
# a cold hit to creator.xiaohongshu.com with only a pro.xiaohongshu.com
# session transiently 401s to a "login" URL while a *silent* CAS service-
# ticket exchange runs in the background, then the SPA redirects itself back
# to the real page once the ticket lands — observed live 2026-07-15 to
# resolve in ~6s. 15s gives headroom above that.
_LOGIN_CHECK_MAX_MS = 15000


def _looks_expired(page) -> bool:
    return (
        XHS_SELECT_ACCOUNT_URL_MARKER in page.url
        or looks_like_login(page.url, visible_text(page))
    )


def _goto_and_check_login(page, url: str) -> bool:
    """Navigate to `url` and poll for up to _LOGIN_CHECK_MAX_MS.

    A login-looking page is only treated as SessionExpiredError if it's
    STILL there once the full wait budget elapses — the transient CAS
    redirect described above means seeing it briefly right after navigation
    is normal and must not short-circuit a real, valid session as expired.
    Returns as soon as a non-login state is observed (the common, fast path
    when no CAS handoff is needed at all).
    """
    page.goto(url, wait_until="domcontentloaded")
    waited = 0
    while waited < _LOGIN_CHECK_MAX_MS:
        page.wait_for_timeout(_LOGIN_CHECK_POLL_MS)
        waited += _LOGIN_CHECK_POLL_MS
        if not _looks_expired(page):
            return False
    return _looks_expired(page)


def collect_xhs(storage_path: Path, *, headless: bool | None = None) -> tuple[bytes, str]:
    """Run one XHS export for the account whose session lives at storage_path.

    Returns (file_bytes, filename). Raises SessionExpiredError or
    DownloadTimeoutError on failure; both leave a debug screenshot+HTML dump.
    """
    with open_context(storage_path, headless=headless) as page:
        # Warm-up hop: the creator.xiaohongshu.com SSO handoff only fires when
        # the live session actually visits pro.xiaohongshu.com first — see
        # module docstring bug (1).
        if _goto_and_check_login(page, XHS_PRO_HOME_URL):
            save_debug_artifacts(page, "xhs_session_expired")
            raise SessionExpiredError(f"XHS session expired (redirected to {page.url!r})")

        if _goto_and_check_login(page, XHS_DATA_URL):
            save_debug_artifacts(page, "xhs_session_expired")
            raise SessionExpiredError(f"XHS session expired (redirected to {page.url!r})")

        try:
            download = expect_download(page, lambda: page.click(XHS_EXPORT_BUTTON))
        except Exception as exc:
            save_debug_artifacts(page, "xhs_download_timeout")
            raise DownloadTimeoutError(f"XHS export download did not complete: {exc}") from exc

        download_path = download.path()
        data = Path(download_path).read_bytes()
        filename = download.suggested_filename or "xhs_export.xlsx"
        return data, filename
