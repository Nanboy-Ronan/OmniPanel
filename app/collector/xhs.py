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
                                  over a date range. collect_xhs_overview()
                                  below reads this one's own JSON APIs
                                  directly (no export button — see that
                                  function's docstring).
     /statistics/data-analysis — 内容分析: per-note (per-post) metrics,
                                  matches XhsPost/parse_xhs_xlsx exactly.
                                  This is XHS_DATA_URL below, collected by
                                  collect_xhs() via its "导出数据" button.
5. Export button text is exactly "导出数据"; clicking it starts the file
   download directly — there is no confirm dialog/second click.

One more real bug found and fixed along the way: launching Chromium with
``--disable-blink-features=AutomationControlled`` (browser.py's original
open_context()) got XHS's risk control to treat an otherwise-valid session
as invalid and force it back to login — no genuine user browser ever has
that flag, so its *presence* was itself the tell. See browser.py.

── Running as the wrong OS user crashes the renderer (found live) ────────
Diagnosing collect_xhs_overview() during development, ANY cross-origin
navigation on creator.xiaohongshu.com — including the long-working
XHS_DATA_URL above — made headless Chromium crash with zero network/console
events, on every sandbox flag combination tried. Root cause: the probes were
run as root over plain SSH; production's collector service runs as an
unprivileged service user. Re-running identically as that user fixed it
immediately for both pages — this was never a page-compatibility issue.
Anyone probing this module by hand on the VM must do the same, and must not
use open_context() for a read-only probe run as a throwaway user: it
unconditionally writes storage_state back to the session file on exit,
and doing that as root once already corrupted a session file's ownership
(the service user couldn't read it back) until manually chown'd back.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .browser import expect_download, looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, SessionExpiredError, WrongAccountError, XhsApiError

XHS_PRO_HOME_URL = "https://pro.xiaohongshu.com/"
XHS_LOGIN_URL = "https://pro.xiaohongshu.com/login"
XHS_ACCOUNT_OVERVIEW_URL = "https://creator.xiaohongshu.com/statistics/account/v2"
XHS_DATA_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
XHS_EXPORT_BUTTON = 'button:has-text("导出数据")'

# ── "数据概览" JSON APIs (collect_xhs_overview) ─────────────────────────────
# Confirmed live, run as the unprivileged collector service user (see module
# docstring addendum below for why that user matters). These are the same
# requests the /statistics/account/v2 SPA fires on load — collect_xhs_overview()
# captures them passively via page.on("response") rather than calling
# page.request.get() on them directly, which was tried first and confirmed
# live to fail with HTTP 406 (see collect_xhs_overview's docstring).
# `account/base`'s "thirty" window turned out to be a superset of every field
# also present on "note_detail_new" (view/like/comment/share/collect/danmaku
# counts) PLUS the fields that endpoint doesn't have at all (loss_fans_count,
# net_rise_fans_count, video_full_view_rate, cover_click_rate, avg_view_time)
# — except the two endpoints' view/like/collect/home_view counts do NOT
# agree with each other (base.seven.view_count vs note_detail_new.seven.
# view_count differed on the same account, same moment; comment_count and
# share_count did match, view/like/collect/home_view did not). Since the
# source of that discrepancy is unknown and mixing the two would silently
# blend two different counting methodologies into one row, this module
# reads every daily field from `account/base` alone.
XHS_ACCOUNT_BASE_API = "https://creator.xiaohongshu.com/api/galaxy/v2/creator/datacenter/account/base"
XHS_AUDIENCE_SOURCE_API = "https://creator.xiaohongshu.com/api/galaxy/v2/creator/datacenter/audience/source/account"

# account/base's "thirty" window returns 30 days of *per-day* values (not a
# running total) — verified live by summing each list field's daily entries
# against the window's own aggregate (e.g. rise_fans_list summed to exactly
# rise_fans_count, both for the 7- and 30-day window). Always collecting the
# 30-day window rather than 7 means a single missed collection day
# self-heals on the next run instead of leaving a permanent gap — the API
# exposes no way to request an arbitrary date range, so the daily rows this
# module produces are only ever "the last 30 days as of today."
_DAILY_FIELD_LISTS = {
    "rise_fans_count": "rise_fans_list",
    "loss_fans_count": "loss_fans_count_list",
    "net_rise_fans_count": "net_rise_fans_count_list",
    "view_count": "view_list",
    "view_time_total_seconds": "view_time_list",
    "avg_view_time_seconds": "avg_view_time_list",
    "home_view_count": "home_view_list",
    "like_count": "like_list",
    "collect_count": "collect_list",
    "comment_count": "comment_list",
    "share_count": "share_list",
    "danmaku_count": "danmaku_list",
    "cover_click_rate": "cover_click_rate_list",
    "video_full_view_rate": "video_full_view_rate_list",
}
# Rate/duration-average fields carry a `count_with_double` alongside the
# rounded integer `count` (e.g. video_full_view_rate 16.5% truncated to 16
# in `count`) — read the precise value for these, plain `count` for
# genuine whole-number tallies.
_DAILY_RATE_FIELDS = {"avg_view_time_seconds", "cover_click_rate", "video_full_view_rate"}

# One phone number can be linked to multiple XHS professional accounts (found
# 2026-07-15: this account's owner has two separate professional accounts). After
# SMS verification, login lands on an intermediate account-picker page at this
# URL before reaching a real dashboard. It is NOT a login page (looks_like_login
# correctly returns False for it) but it is also NOT a completed login — the
# resulting session is only valid once a specific account has been clicked.
# bootstrap-login's poll loop must wait through this page, not stop at it.
XHS_SELECT_ACCOUNT_URL_MARKER = "select-account"

# Found live debugging a silent wrong-account bootstrap-login: on the
# horizontal multi-account picker, matching the "立即登录" button by
# y-proximity (all buttons share the same y) always resolved to whichever
# button came first in DOM order -- the *personal* account, not the target
# business one -- and every existing "is this session valid" signal passed
# anyway (same pro.xiaohongshu.com/enterprise/home URL either way; not a
# login page either way). The only observable difference is this exact body
# text, shown instead of real data whenever the current account lacks
# professional/business permissions. This is a TERMINAL state, not a
# transient CAS-handoff one like the login-page flicker _looks_expired()
# absorbs -- it must not be polled through, and it means something
# different from "session expired" (see WrongAccountError).
XHS_WRONG_ACCOUNT_MARKER = "没有查看当前页面的权限"

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


def _check_wrong_account(page) -> None:
    """Raise WrongAccountError if the current page shows the "no permission"
    marker instead of real data. Call this ONLY after _goto_and_check_login
    has already confirmed the page is not (still) a login/CAS-handoff state
    — this is a one-shot terminal check, not something to poll through."""
    try:
        text = visible_text(page)
    except Exception:
        return
    if XHS_WRONG_ACCOUNT_MARKER in text:
        raise WrongAccountError(
            f"session is logged in but resolves to the wrong account "
            f"(page shows {XHS_WRONG_ACCOUNT_MARKER!r} instead of real data) "
            f"— re-run bootstrap-login and pick the correct 子账号; "
            f"re-collecting against this same session file will not fix it"
        )


def verify_xhs_session(storage_path: Path, *, headless: bool | None = None) -> bool:
    """Check whether the saved session at storage_path is still logged in.

    Read-only probe: reuses the exact same warm-up hop + 15s CAS wait budget
    as collect_xhs() (see module docstring bug (1) and (3)) instead of the
    naive single-goto-and-check that the original verify-session had, which
    would misreport a perfectly valid session as expired because it never
    gave the silent CAS ticket exchange time to finish. Does not download or
    write debug artifacts.

    Raises WrongAccountError (rather than just returning False) if the
    session is live but on the wrong account — this is not the same problem
    as an expired session and the caller should tell the human something
    different about it. See module docstring / WrongAccountError.
    """
    with open_context(storage_path, headless=headless) as page:
        if _goto_and_check_login(page, XHS_PRO_HOME_URL):
            return False
        if _goto_and_check_login(page, XHS_DATA_URL):
            return False
        _check_wrong_account(page)
        return True


def collect_xhs(storage_path: Path, *, headless: bool | None = None) -> tuple[bytes, str]:
    """Run one XHS export for the account whose session lives at storage_path.

    Returns (file_bytes, filename). Raises SessionExpiredError,
    WrongAccountError, or DownloadTimeoutError on failure; all three leave a
    debug screenshot+HTML dump.
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
            _check_wrong_account(page)
        except WrongAccountError:
            save_debug_artifacts(page, "xhs_wrong_account")
            raise

        try:
            download = expect_download(page, lambda: page.click(XHS_EXPORT_BUTTON))
        except Exception as exc:
            save_debug_artifacts(page, "xhs_download_timeout")
            raise DownloadTimeoutError(f"XHS export download did not complete: {exc}") from exc

        download_path = download.path()
        data = Path(download_path).read_bytes()
        filename = download.suggested_filename or "xhs_export.xlsx"
        return data, filename


# How long to keep polling for the SPA's own account/base + audience/source
# requests to land after the page looks logged-in. Confirmed live that a
# bare page.request.get() on these URLs (reusing only the session's cookie
# jar, no page-JS-computed headers) gets rejected with HTTP 406 —
# xiaohongshu's API layer expects whatever signature/header the SPA's own
# fetch call attaches. So these are captured passively via page.on("response")
# instead of fetched directly; this budget is how long to wait for the SPA
# to have actually fired them once the page is live.
_API_CAPTURE_POLL_MS = 500
_API_CAPTURE_MAX_MS = 10000


def _parse_api_body(url: str, body: dict) -> dict:
    """Validate one captured response body and return its `data` payload.
    Raises XhsApiError on a non-zero business code — a live, correctly-
    authenticated session that the endpoint is nonetheless refusing (e.g.
    permissions changed mid-session). Deliberately not SessionExpiredError:
    re-running bootstrap-login is not known to fix this and the operator
    needs to see the raw API error to tell the two apart."""
    if body.get("code") not in (0, None):
        raise XhsApiError(f"{url} returned code={body.get('code')} msg={body.get('msg')!r}")
    return body.get("data") or {}


def _shanghai_today() -> date:
    """The current calendar date in Asia/Shanghai — NOT dt.date.today(),
    which is the server's local (UTC) date. The daily collector runs early
    Beijing morning, still the previous day in UTC, so date.today() on the
    server would tag every snapshot_date one day behind the metric_date
    values _local_date_from_ms already correctly computes in Shanghai time
    — same bug class, different field, caught before deploy."""
    return datetime.now(tz=ZoneInfo("Asia/Shanghai")).date()


def _local_date_from_ms(ms: int) -> date:
    """XHS's daily list entries carry a UTC-millisecond timestamp for local
    (Asia/Shanghai) midnight of that day, not a UTC calendar date — e.g. a
    timestamp of local midnight is already 16:00 UTC the previous day.
    Naively taking the UTC calendar date is off by one for any entry whose
    UTC time-of-day is >= 16:00, i.e. always, since these are always local
    midnight. Confirmed against account/base's own begin_time/end_time and
    against the entries' own date ordering live."""
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo("Asia/Shanghai")).date()


def _build_daily_rows(base_thirty: dict) -> list[dict]:
    """Reshape account/base's thirty-window column-of-lists shape (one list
    per field, each list holding {date, count, count_with_double}) into one
    row per calendar date, keyed by the fields in _DAILY_FIELD_LISTS."""
    by_date: dict[str, dict] = {}
    for field, list_key in _DAILY_FIELD_LISTS.items():
        for entry in base_thirty.get(list_key) or []:
            metric_date = _local_date_from_ms(entry["date"]).isoformat()
            row = by_date.setdefault(metric_date, {"metric_date": metric_date})
            if field in _DAILY_RATE_FIELDS:
                row[field] = entry.get("count_with_double", entry.get("count"))
            else:
                row[field] = entry.get("count")
    return sorted(by_date.values(), key=lambda r: r["metric_date"])


def _parse_audience_source(data: dict) -> list[dict]:
    """audience/source/account has no daily list — each window's entries are
    a percentage-share breakdown ("of views/growth over the last N days,
    X% came from source Y") as of the moment collected, not a count. Kept as
    a single day's snapshot per window rather than forced into the daily
    metrics table."""
    rows = []
    for window in ("seven", "thirty"):
        for entry in data.get(window) or []:
            rows.append({
                "window": window,
                "source_type": entry.get("source_type"),
                "title": entry.get("title"),
                "value_pct": entry.get("value"),
            })
    return rows


def collect_xhs_overview(storage_path: Path, *, headless: bool | None = None) -> tuple[bytes, str]:
    """Fetch account-level "数据概览" metrics for the account at storage_path.

    Unlike collect_xhs(), this never clicks an export button — but unlike an
    earlier version of this function, it also does NOT call page.request.get()
    directly: that bare cookie-jar GET was confirmed live to get rejected
    with HTTP 406 (xiaohongshu's API layer wants whatever header/signature
    the SPA's own JS-issued fetch attaches, which page.request does not
    replicate). Instead, a page.on("response") listener is registered
    *before* navigating, and the SPA's own account/base + audience/source
    requests — which it fires on page load regardless — are captured
    passively once they land. This is the same technique the diagnostic
    probes that first proved this page's JSON APIs exist used successfully;
    the direct-GET shortcut was an unverified assumption on top of that, is
    what this docstring is warning future editors against reintroducing.

    Returns (file_bytes, filename) — the same shape collect_xhs() returns —
    so it slots into the existing collect → upload runner plumbing
    unchanged; the bytes are UTF-8 JSON (not an xlsx) holding:
      {"daily": [...30 rows, one per calendar date...],
       "audience_source": [...seven+thirty window breakdown rows...]}
    Raises SessionExpiredError, WrongAccountError, or XhsApiError on
    failure; all three leave a debug screenshot+HTML dump.
    """
    with open_context(storage_path, headless=headless) as page:
        captured: dict[str, dict] = {}

        def _capture(response) -> None:
            url = response.url
            for target in (XHS_ACCOUNT_BASE_API, XHS_AUDIENCE_SOURCE_API):
                if target in captured or not url.startswith(target):
                    continue
                try:
                    captured[target] = response.json()
                except Exception:
                    pass  # not this response, or a non-JSON body — ignore and keep waiting

        page.on("response", _capture)

        if _goto_and_check_login(page, XHS_PRO_HOME_URL):
            save_debug_artifacts(page, "xhs_overview_session_expired")
            raise SessionExpiredError(f"XHS session expired (redirected to {page.url!r})")

        if _goto_and_check_login(page, XHS_ACCOUNT_OVERVIEW_URL):
            save_debug_artifacts(page, "xhs_overview_session_expired")
            raise SessionExpiredError(f"XHS session expired (redirected to {page.url!r})")

        try:
            _check_wrong_account(page)
        except WrongAccountError:
            save_debug_artifacts(page, "xhs_overview_wrong_account")
            raise

        waited = 0
        while waited < _API_CAPTURE_MAX_MS and not (
            XHS_ACCOUNT_BASE_API in captured and XHS_AUDIENCE_SOURCE_API in captured
        ):
            page.wait_for_timeout(_API_CAPTURE_POLL_MS)
            waited += _API_CAPTURE_POLL_MS

        missing = [
            url for url in (XHS_ACCOUNT_BASE_API, XHS_AUDIENCE_SOURCE_API) if url not in captured
        ]
        if missing:
            save_debug_artifacts(page, "xhs_overview_api_not_observed")
            raise XhsApiError(
                f"the SPA never fired (or never returned JSON for) {missing} within "
                f"{_API_CAPTURE_MAX_MS}ms of the page looking logged in"
            )

        try:
            base = _parse_api_body(XHS_ACCOUNT_BASE_API, captured[XHS_ACCOUNT_BASE_API])
            audience_source = _parse_api_body(XHS_AUDIENCE_SOURCE_API, captured[XHS_AUDIENCE_SOURCE_API])
        except XhsApiError:
            save_debug_artifacts(page, "xhs_overview_api_error")
            raise

    snapshot_date = _shanghai_today()
    payload = {
        "daily": _build_daily_rows(base.get("thirty") or {}),
        "audience_source": _parse_audience_source(audience_source),
        # audience_source carries no per-entry date of its own (see
        # _parse_audience_source) — this is "as of" date the ETL layer
        # stamps those rows with, computed here (not server-side at upload
        # time) so it reflects when the data was actually observed
        # regardless of what timezone the API process happens to run in.
        "snapshot_date": snapshot_date.isoformat(),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    filename = f"xhs_overview_{snapshot_date.isoformat()}.json"
    return data, filename
