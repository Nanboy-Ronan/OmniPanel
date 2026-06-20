from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import requests
from dotenv import load_dotenv


API_BASE = "https://api.weixin.qq.com"


def _accounts() -> list[dict[str, str]]:
    load_dotenv()
    accounts: list[dict[str, str]] = []
    for idx in range(1, 11):
        app_id = os.getenv(f"WECHAT_APP_ID_{idx}")
        app_secret = os.getenv(f"WECHAT_APP_SECRET_{idx}")
        if not app_id or not app_secret:
            continue
        accounts.append(
            {
                "slot": str(idx),
                "name": os.getenv(f"WECHAT_ACCOUNT_NAME_{idx}", f"WeChat Account {idx}"),
                "app_id": app_id,
                "app_secret": app_secret,
            }
        )
    return accounts


def _raise_wechat_error(payload: dict[str, Any]) -> None:
    if payload.get("errcode"):
        raise RuntimeError(f"{payload.get('errcode')}: {payload.get('errmsg')}")


def _token(app_id: str, app_secret: str) -> str:
    response = requests.get(
        f"{API_BASE}/cgi-bin/token",
        params={
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret,
        },
        timeout=15,
    )
    response.encoding = "utf-8"
    payload = response.json()
    _raise_wechat_error(payload)
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"access_token missing: {payload}")
    return str(token)


def _post(endpoint: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{API_BASE}/{endpoint}",
        params={"access_token": token},
        json=payload,
        timeout=20,
    )
    response.encoding = "utf-8"
    data = response.json()
    _raise_wechat_error(data)
    return data


def _safe_error(exc: Exception, account: dict[str, str]) -> str:
    message = str(exc)
    for secret in (account.get("app_secret"), account.get("app_id")):
        if secret:
            message = message.replace(secret, "***")
    return message


def main() -> None:
    accounts = _accounts()
    if not accounts:
        raise SystemExit("No WECHAT_APP_ID_N / WECHAT_APP_SECRET_N accounts found in .env")

    metric_day = (date.today() - timedelta(days=1)).isoformat()
    print(f"Testing {len(accounts)} WeChat Official Account(s), metric day {metric_day}")

    for account in accounts:
        name = account["name"]
        app_id = account["app_id"]
        print(f"\n[{account['slot']}] {name} ({app_id})")
        try:
            token = _token(app_id, account["app_secret"])
            print("  token: ok")

            published = _post(
                "cgi-bin/freepublish/batchget",
                token,
                {"offset": 0, "count": 20, "no_content": 1},
            )
            item_count = len(published.get("item", []))
            total_count = published.get("total_count")
            print(f"  freepublish.batchget: ok, returned={item_count}, total={total_count}")

            article_total = _post(
                "datacube/getarticletotaldetail",
                token,
                {"begin_date": metric_day, "end_date": metric_day},
            )
            items = article_total.get("list", [])
            detail_rows = sum(len(item.get("detail_list", [])) for item in items)
            print(f"  datacube.getarticletotaldetail: ok, articles={len(items)}, daily_rows={detail_rows}")
        except Exception as exc:
            print(f"  failed: {_safe_error(exc, account)}")


if __name__ == "__main__":
    main()
