from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)

WECHAT_API_BASE = "https://api.weixin.qq.com"

# WeChat DataCube error codes that mean "no data for this date" rather than a
# real failure.  We skip these dates silently instead of aborting the whole sync.
_SKIP_ERROR_CODES = {
    61501,  # "date range incorrect" — data not yet available (1-2 day lag)
    61517,  # "no data" for the requested date
}


class WeChatAPIError(RuntimeError):
    """Raised when the WeChat Official Account API returns an error payload."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.code: int | None = payload.get("errcode")
        message = payload.get("errmsg") or payload.get("message") or "unknown error"
        super().__init__(f"WeChat API error {self.code}: {message}")


def _wechat_json(response: requests.Response) -> dict[str, Any]:
    response.encoding = "utf-8"
    return response.json()


def iter_dates(start_date: date, end_date: date) -> Iterable[date]:
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(value)


def normalize_article_total_detail_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one `getarticletotaldetail` item into daily metric rows.

    The API returns one item per article (identified by msgid), with a
    detail_list that covers every stat day from the publish date to now.
    begin_date/end_date in the request refer to article publish dates.
    """
    publish_date = _parse_date(item.get("ref_date"))
    external_id = str(item.get("msgid") or "")
    title = str(item.get("title") or "Untitled")
    url = item.get("content_url") or None
    publish_type = item.get("publish_type")

    rows: list[dict[str, Any]] = []
    for detail in item.get("detail_list") or []:
        metric_date = _parse_date(detail.get("stat_date"))
        if not metric_date:
            continue
        rows.append(
            {
                "external_id": external_id,
                "title": title,
                "publish_date": publish_date,
                "metric_date": metric_date,
                "url": url,
                "publish_type": publish_type,
                "read_user_count": _int_value(detail.get("read_user")),
                "share_user_count": _int_value(detail.get("share_user")),
                "like_user": _int_value(detail.get("like_user")),
                "comment_count": _int_value(detail.get("comment_count")),
                "collection_user": _int_value(detail.get("collection_user")),
                "read_avg_time": detail.get("read_avg_activetime"),
                "read_user_source": detail.get("read_user_source"),
                "zaikan_user": _int_value(detail.get("zaikan_user")),
                "read_subscribe_user": _int_value(detail.get("read_subscribe_user")),
                "read_delivery_rate": detail.get("read_delivery_rate"),
                "praise_money": _int_value(detail.get("praise_money")),
                "read_jump_position": detail.get("read_jump_position"),
                "read_finish_rate": detail.get("read_finish_rate"),
                "raw_payload": detail,
            }
        )
    return rows


_DEFAULT_WECHAT_TIMEOUT = float(os.getenv("WECHAT_HTTP_TIMEOUT", "10"))


@dataclass
class WeChatOfficialClient:
    app_id: str
    app_secret: str
    timeout: float = field(default_factory=lambda: _DEFAULT_WECHAT_TIMEOUT)

    def get_access_token(self) -> str:
        response = requests.get(
            f"{WECHAT_API_BASE}/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": self.app_id,
                "secret": self.app_secret,
            },
            timeout=self.timeout,
        )
        payload = _wechat_json(response)
        if payload.get("errcode"):
            raise WeChatAPIError(payload)
        token = payload.get("access_token")
        if not token:
            raise WeChatAPIError({"errcode": "missing_access_token", "errmsg": str(payload)})
        return str(token)

    def _post_datacube(self, endpoint: str, access_token: str, day: date) -> dict[str, Any]:
        response = requests.post(
            f"{WECHAT_API_BASE}/datacube/{endpoint}",
            params={"access_token": access_token},
            json={"begin_date": day.isoformat(), "end_date": day.isoformat()},
            timeout=self.timeout,
        )
        payload = _wechat_json(response)
        errcode = payload.get("errcode")
        if errcode:
            raise WeChatAPIError(payload)
        return payload

    def fetch_published_article_dates(self, window_days: int = 170) -> list[date]:
        """Return publish dates of articles still within WeChat's DataCube retention window.

        Calls ``freepublish/batchget`` (paginated) and returns only dates that fall
        within the last *window_days* days.  Dates outside the window are excluded
        because WeChat has already purged their metrics.

        Results are sorted ascending so callers can iterate oldest-first.
        """
        cutoff = date.today() - timedelta(days=window_days)
        access_token = self.get_access_token()
        dates: set[date] = set()
        offset = 0
        page_size = 20

        while True:
            response = requests.post(
                f"{WECHAT_API_BASE}/cgi-bin/freepublish/batchget",
                params={"access_token": access_token},
                json={"offset": offset, "count": page_size, "no_content": 1},
                timeout=self.timeout,
            )
            payload = _wechat_json(response)
            if payload.get("errcode"):
                raise WeChatAPIError(payload)

            items = payload.get("item", [])
            total = payload.get("total_count", 0)

            for art in items:
                ts = art.get("update_time")
                if ts:
                    pub_date = datetime.fromtimestamp(ts).date()
                    if pub_date >= cutoff:
                        dates.add(pub_date)

            offset += len(items)
            if offset >= total or not items:
                break

        return sorted(dates)

    def fetch_article_total_rows(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """Fetch full per-article daily metrics via getarticletotaldetail.

        start_date/end_date are article publish dates (not stat dates).
        Each returned row covers one (article, stat_date) pair and includes
        the complete history from the article's publish date to the present.

        WeChat DataCube has a 1-2 day data lag, so we automatically cap
        end_date to yesterday.  Dates that return a "no data" error code
        (e.g. 61501) are skipped silently rather than aborting the sync.
        """
        # WeChat data is typically only available up to yesterday
        yesterday = date.today() - timedelta(days=1)
        if end_date > yesterday:
            logger.info(
                "Capping WeChat sync end_date from %s to %s (data lag)", end_date, yesterday
            )
            end_date = yesterday
        if start_date > end_date:
            logger.warning("WeChat sync: start_date %s is after capped end_date %s; nothing to do", start_date, end_date)
            return []

        access_token = self.get_access_token()
        rows: list[dict[str, Any]] = []
        for day in iter_dates(start_date, end_date):
            try:
                payload = self._post_datacube("getarticletotaldetail", access_token, day)
            except WeChatAPIError as exc:
                if exc.code in _SKIP_ERROR_CODES:
                    logger.debug("Skipping %s — WeChat errcode %s", day, exc.code)
                    continue
                raise
            for item in payload.get("list", []):
                rows.extend(normalize_article_total_detail_item(item))
        return rows
