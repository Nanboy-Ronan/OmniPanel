"""Zhihu (知乎) creator-portal export collector.

One login serves both content types; article and qa exports are two
separate trips through the same data page, selecting a different tab before
triggering the download. Zhihu's export is a UTF-8-BOM CSV often named
.xls — pass the bytes through unchanged, app/db/etl/zhihu._read_zhihu_file
already handles that quirk.

── Portal URLs & selectors ──────────────────────────────────────────────
Every value below is a PLACEHOLDER, unverified against the live portal.
Confirm during the Phase-1/3 PoC (docs/collector.md) and update only this
block.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from .browser import expect_download, looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, SessionExpiredError

ZHIHU_DATA_URL = "https://www.zhihu.com/creator/data-center"
ZHIHU_TAB = {
    "article": 'text=文章',
    "qa": 'text=回答',
}
ZHIHU_EXPORT_BUTTON = 'button:has-text("导出")'


def verify_zhihu_session(storage_path: Path, *, headless: bool | None = None) -> bool:
    """Check whether the saved session at storage_path is still logged in.

    Read-only probe mirroring collect_zhihu()'s login check. Zhihu's
    selectors are still unverified placeholders (see module docstring), so
    this stays as simple as collect_zhihu's own check rather than inventing
    unverified CAS-style handling XHS needed.
    """
    with open_context(storage_path, headless=headless) as page:
        page.goto(ZHIHU_DATA_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        return not looks_like_login(page.url, visible_text(page))


def collect_zhihu(
    storage_path: Path,
    content_type: Literal["article", "qa"],
    *,
    headless: bool | None = None,
) -> tuple[bytes, str]:
    """Run one Zhihu export (article or qa) for the shared knowledge-base login.

    Returns (file_bytes, filename). Raises SessionExpiredError or
    DownloadTimeoutError on failure; both leave a debug screenshot+HTML dump.
    """
    with open_context(storage_path, headless=headless) as page:
        page.goto(ZHIHU_DATA_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        if looks_like_login(page.url, visible_text(page)):
            save_debug_artifacts(page, f"zhihu_session_expired_{content_type}")
            raise SessionExpiredError(f"Zhihu session expired (redirected to {page.url!r})")

        try:
            download = expect_download(
                page,
                lambda: (
                    page.click(ZHIHU_TAB[content_type]),
                    page.click(ZHIHU_EXPORT_BUTTON),
                ),
            )
        except Exception as exc:
            save_debug_artifacts(page, f"zhihu_download_timeout_{content_type}")
            raise DownloadTimeoutError(f"Zhihu {content_type} export download did not complete: {exc}") from exc

        download_path = download.path()
        data = Path(download_path).read_bytes()
        filename = download.suggested_filename or f"zhihu_{content_type}_export.csv"
        return data, filename
