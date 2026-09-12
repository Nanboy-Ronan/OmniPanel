"""WeChat Channels (视频号) creator-portal export collector.

Verified live 2026-08-13 against a real 视频号 account. Matches the
confidence level of xhs.py/pugongying.py, not the earlier "unverified
placeholder" draft this module started as (see git history if curious what
was guessed vs. confirmed).

── Auth flow ──────────────────────────────────────────────────────────
1. Login is at https://channels.weixin.qq.com/platform, which redirects an
   unauthenticated session to https://channels.weixin.qq.com/login.html — a
   QR code scanned with the WeChat account bound to the video account (not
   phone+SMS like XHS). generic looks_like_login() already catches this via
   the "login" URL substring, no channels-specific marker needed.
2. Once scanned+confirmed, the SPA redirects to CHANNELS_HOME_URL
   (.../platform) showing the account dashboard (video/follower counts).

── Why this doesn't click "下载表格" ──────────────────────────────────────
An earlier version of this module clicked through the UI end to end:
sidebar "数据中心" (accordion) → "视频数据" → tab "单篇视频" → button
"下载表格" (NOT "导出" — every other collected platform uses that word;
视频号助手 alone doesn't) — the same click-a-button shape every other
platform module here has. It worked, but the button's own date-range filter
defaults to **近7天 (last 7 days)**, and widening it means driving a
finicky WeUI calendar widget (readonly text inputs; the actual day cells
live behind ambiguous DOM structure that made even careful Playwright
locators land on the wrong month — verified live, repeatedly).

Network inspection of that click found something better: "下载表格" is
client-side sugar over a plain authenticated JSON endpoint —
`POST /micro/statistic/cgi-bin/mmfinderassistant-bin/statistic/
download_post_data` with a `{startTime, endTime}` unix-seconds body, called
from within the `micro/statistic/post` iframe's origin. Calling it directly
with a wide range:
  - needs zero calendar-widget automation (the whole failure mode above),
  - returns full account history in **one call** — confirmed live: the whole
    back catalog back to the account's first-ever post, vs. 1 video from the
    UI's "近7天" default,
  - and returns already-typed JSON (float completion_rate, float seconds
    duration) instead of "4.41%"/"14.73秒" strings to unparse.

The one real gap: this JSON has no 企微链接点击/添加到通讯录 fields at all
(checked a full account-wide response for those keys — absent everywhere).
Those four columns exist only in the CSV a human gets from clicking "下载
表格" themselves; app/db/etl/channels.py's CSV parser stays for that manual
path. See that module's docstring for the full field-mapping cross-check.

Still required before the API call: navigating to the 视频数据 tab at all
(homepage → 数据中心 → 视频数据), because the whole page is a Tencent
"WuJie" (无界) micro-frontend and the `micro/statistic/post` iframe (whose
origin the fetch must run from, and whose cookies/session state make the
call authenticated) does not exist until that navigation happens. That
iframe's content takes a highly variable **12-20+ seconds** to finish
loading — _wait_for_text()'s polling loop exists specifically because a
fixed wait_for_timeout(6000) like the other platforms use is NOT enough.
Confirmed NOT the cause: headless vs. headed Chromium (unlike XHS's real
anti-automation flagging documented in browser.py) — headless=True renders
the same content, just as slowly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .browser import looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, SessionExpiredError

CHANNELS_HOME_URL = "https://channels.weixin.qq.com/platform"
CHANNELS_LOGIN_URL = "https://channels.weixin.qq.com/platform"  # redirects to /login.html when unauthenticated
CHANNELS_DATA_NAV = "数据中心"
CHANNELS_DATA_SUBNAV = "视频数据"
CHANNELS_SINGLE_VIDEO_TAB = "单篇视频"
CHANNELS_EXPORT_BUTTON_TEXT = "下载表格"  # used only as a load-complete signal now, never clicked
CHANNELS_STAT_FRAME_URL_MARKER = "micro/statistic/post"

_LOGIN_CHECK_POLL_MS = 1000
_LOGIN_CHECK_MAX_MS = 15000

# The WuJie micro-frontend's real load time observed live: anywhere from
# ~12s to ~20s after clicking into 视频数据. 40s gives real headroom above
# that without hanging forever on a genuinely broken page.
_CONTENT_WAIT_MAX_MS = 40000
_CONTENT_WAIT_POLL_MS = 2000

# How far back to ask for. No reliable way to read "account creation date"
# up front, so this just asks for more than any real account could have —
# confirmed live against a query starting well before the account's actual
# first post, with no error and no truncation.
_LOOKBACK_SECONDS = 5 * 365 * 24 * 3600


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


def _wait_for_text(page, text: str, *, max_wait_ms: int = _CONTENT_WAIT_MAX_MS) -> bool:
    """Poll for `text` to appear anywhere on the page (pierces the WuJie
    iframe/shadow-DOM the same way page.get_by_text always does for content
    actually attached to the main document). Returns False on timeout rather
    than raising — callers decide whether that's a hard failure."""
    waited = 0
    while waited < max_wait_ms:
        if page.get_by_text(text, exact=True).count() > 0:
            return True
        page.wait_for_timeout(_CONTENT_WAIT_POLL_MS)
        waited += _CONTENT_WAIT_POLL_MS
    return False


def _navigate_to_video_data(page) -> None:
    """From CHANNELS_HOME_URL, click through to 视频数据 and wait for the
    WuJie iframe's content to actually finish loading (see module
    docstring). Raises RuntimeError on timeout; caller wraps the context."""
    page.locator("a[class*='menu']", has_text=CHANNELS_DATA_NAV).first.click(timeout=5000)
    page.wait_for_timeout(600)
    page.locator("a[class*='menu']", has_text=CHANNELS_DATA_SUBNAV).first.click(timeout=5000)

    if not _wait_for_text(page, CHANNELS_SINGLE_VIDEO_TAB):
        raise RuntimeError(f"{CHANNELS_SINGLE_VIDEO_TAB!r} tab never appeared (WuJie content load timeout)")


def _find_stat_frame(page):
    for f in page.frames:
        if CHANNELS_STAT_FRAME_URL_MARKER in f.url:
            return f
    return None


def verify_channels_session(storage_path: Path, *, headless: bool | None = None) -> bool:
    with open_context(storage_path, headless=headless) as page:
        return not _goto_and_check_login(page, CHANNELS_HOME_URL)


def collect_channels(storage_path: Path, *, headless: bool | None = None) -> tuple[bytes, str]:
    """Fetch full per-video history for the account whose session lives at
    storage_path, via the direct JSON API (see module docstring for why —
    not a UI-click download like every other platform module here).

    Returns (json_bytes, filename) — the raw API response body, consumed by
    app.db.etl.channels.parse_channels_api_json. Raises SessionExpiredError
    or DownloadTimeoutError on failure; both leave a debug screenshot+HTML
    dump.
    """
    with open_context(storage_path, headless=headless) as page:
        if _goto_and_check_login(page, CHANNELS_HOME_URL):
            save_debug_artifacts(page, "channels_session_expired")
            raise SessionExpiredError(f"WeChat Channels session expired (redirected to {page.url!r})")

        try:
            _navigate_to_video_data(page)
        except Exception as exc:
            save_debug_artifacts(page, "channels_nav_failed")
            raise DownloadTimeoutError(f"WeChat Channels navigation to 视频数据 failed: {exc}") from exc

        stat_frame = _find_stat_frame(page)
        if stat_frame is None:
            save_debug_artifacts(page, "channels_no_stat_frame")
            raise DownloadTimeoutError("WeChat Channels: micro/statistic/post iframe never appeared")

        now = int(time.time())
        start = now - _LOOKBACK_SECONDS

        try:
            result = stat_frame.evaluate(
                """
                async ({start, end}) => {
                  const resp = await fetch(
                    '/micro/statistic/cgi-bin/mmfinderassistant-bin/statistic/download_post_data'
                    + '?_aid=collector&_rid=collector&_pageUrl=' + encodeURIComponent(location.href),
                    {
                      method: 'POST',
                      headers: {'Content-Type': 'application/json'},
                      credentials: 'include',
                      body: JSON.stringify({
                        startTime: start, endTime: end, timestamp: String(Date.now()),
                        _log_finder_uin: '', _log_finder_id: '', rawKeyBuff: '',
                        pluginSessionId: null, scene: 7, reqScene: 7
                      })
                    }
                  );
                  const text = await resp.text();
                  return {status: resp.status, text};
                }
                """,
                {"start": start, "end": now},
            )
        except Exception as exc:
            save_debug_artifacts(page, "channels_api_call_failed")
            raise DownloadTimeoutError(f"WeChat Channels download_post_data call failed: {exc}") from exc

        if result.get("status") != 201:
            save_debug_artifacts(page, "channels_api_bad_status")
            raise DownloadTimeoutError(
                f"WeChat Channels download_post_data returned status {result.get('status')}"
            )

        try:
            parsed = json.loads(result["text"])
        except json.JSONDecodeError as exc:
            save_debug_artifacts(page, "channels_api_bad_json")
            raise DownloadTimeoutError(f"WeChat Channels download_post_data returned non-JSON body: {exc}") from exc

        if parsed.get("errCode") != 0:
            save_debug_artifacts(page, "channels_api_err_code")
            raise DownloadTimeoutError(
                f"WeChat Channels download_post_data errCode={parsed.get('errCode')} errMsg={parsed.get('errMsg')!r}"
            )

        data = result["text"].encode("utf-8")
        filename = "channels_video_data.json"
        return data, filename
