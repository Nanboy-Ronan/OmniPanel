"""Zhihu (知乎) organization-portal export collector.

Verified live against a real 知乎机构号 (organization) account — see
docs/collector.md's validation log for the full narrative. One login serves
both content types; Zhihu's export is a UTF-8-BOM CSV often named .xls —
pass the bytes through unchanged, app/db/etl/zhihu._read_zhihu_file already
handles that quirk.

── What the account actually is ─────────────────────────────────────────
A 知乎机构号 (organization account) login is not a personal creator
account. That matters because it lives under a completely different URL
tree than the original (guessed, never-verified) `/creator` constant this
module shipped with: hitting `/creator` with an org account doesn't
redirect anywhere odd, it loads and shows a plain rejection message,
"该账号类型无法开通创作中心" ("this account type cannot open Creator
Center") — no crash, no login-looking URL, so `looks_like_login()` doesn't
catch it either. A future Zhihu account added to this system needs the same
one-time check (open `/creator` logged in and read the body text) before
assuming it's an organization account too.

── The export flow ───────────────────────────────────────────────────────
The real per-post data lives at `/organization/analytics/work/{article,
answer}`. That page defaults to an *aggregated* view ("所有XX分析") whose
own "导出 Excel" button — same visible text, same page — downloads a 7-row
daily-total sheet with no title/url at all. The per-post view needed here
("单篇XX分析") is a second tab on the same page, reachable directly by
adding `?tab=single` to the URL (confirmed live: no need to click the tab).
Its "导出 Excel" export matches the existing manually-uploaded fixtures
(`data/zhihu_article.xls` / `data/zhihu_qa.xls`) column-for-column, one row
per post. Capped at the latest 1000 items server-side (own page copy:
"仅支持导出最新 1000 条内容") — not handled specially here since neither
content type is near that yet.

The export control itself renders as a `<div>` with an inline SVG icon and
the text "导出 Excel", **not** a `<button>` — `button:has-text(...)` (the
original guess) never matches anything on the live page. Use a bare `text=`
locator, which matches on rendered text regardless of tag.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from .browser import expect_download, looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, SessionExpiredError

# Any authenticated organization-center page works here — bootstrap-login's
# landing page and verify_zhihu_session's login probe don't care which one,
# unlike collect_zhihu which needs the exact per-content-type URL below.
ZHIHU_HOME_URL = "https://www.zhihu.com/organization/question/hot"

ZHIHU_CONTENT_URL = {
    "article": "https://www.zhihu.com/organization/analytics/work/article?tab=single",
    "qa": "https://www.zhihu.com/organization/analytics/work/answer?tab=single",
}
ZHIHU_EXPORT_BUTTON = "text=导出 Excel"
# Present only on the per-post ("单篇XX分析") view, never on the page's
# default aggregate view — the two views share the exact same
# ZHIHU_EXPORT_BUTTON text, so this is the only cheap way to tell them apart
# before clicking export. Asserting on it turns a silent wrong-view export
# (aggregate's "导出 Excel" downloads a 7-row daily total with no
# title/url — parses "successfully" into garbage, no alert) into a loud
# DownloadTimeoutError instead. See docs/collector.md validation log.
ZHIHU_SINGLE_VIEW_MARKER = "text=仅支持导出最新 1000 条内容"


def verify_zhihu_session(storage_path: Path, *, headless: bool | None = None) -> bool:
    """Check whether the saved session at storage_path is still logged in.

    Read-only probe mirroring collect_zhihu()'s login check.
    """
    with open_context(storage_path, headless=headless) as page:
        page.goto(ZHIHU_HOME_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        return not looks_like_login(page.url, visible_text(page))


def collect_zhihu(
    storage_path: Path,
    content_type: Literal["article", "qa"],
    *,
    headless: bool | None = None,
) -> tuple[bytes, str]:
    """Run one Zhihu export (article or qa) for the shared organization login.

    Returns (file_bytes, filename). Raises SessionExpiredError or
    DownloadTimeoutError on failure; both leave a debug screenshot+HTML dump.
    """
    with open_context(storage_path, headless=headless) as page:
        page.goto(ZHIHU_CONTENT_URL[content_type], wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        if looks_like_login(page.url, visible_text(page)):
            save_debug_artifacts(page, f"zhihu_session_expired_{content_type}")
            raise SessionExpiredError(f"Zhihu session expired (redirected to {page.url!r})")

        try:
            page.wait_for_selector(ZHIHU_SINGLE_VIEW_MARKER)
        except Exception as exc:
            save_debug_artifacts(page, f"zhihu_wrong_view_{content_type}")
            raise DownloadTimeoutError(
                f"Zhihu {content_type} page never showed the per-post view "
                f"(?tab=single may not have applied) — refusing to export "
                f"from the aggregate view: {exc}"
            ) from exc

        try:
            download = expect_download(page, lambda: page.click(ZHIHU_EXPORT_BUTTON))
        except Exception as exc:
            save_debug_artifacts(page, f"zhihu_download_timeout_{content_type}")
            raise DownloadTimeoutError(f"Zhihu {content_type} export download did not complete: {exc}") from exc

        download_path = download.path()
        data = Path(download_path).read_bytes()
        filename = download.suggested_filename or f"zhihu_{content_type}_export.csv"
        return data, filename
