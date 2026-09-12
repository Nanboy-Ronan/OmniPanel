"""JD/Jingmai order-list collector.

JD's order-detail export center was verified live on 2026-08-30. Even when
"不含收货人信息" is selected, every order-detail export is encrypted and its
password is delivered by SMS. That makes the export flow unsuitable for an
unattended collector.

This module therefore reads the already-rendered, masked order cards from
京麦商家中心 → 订单管理 → 订单列表. It never requests an export password and
never attempts to recover unmasked personal information. The generated CSV
uses the headers accepted by the existing JD ecommerce ETL. The order page's
default six-month window is collected on every run; row/order deduplication in
the existing ingestion pipeline makes this safe and gives a wide retry window.
"""
from __future__ import annotations

import csv
import io
import re
import time
from pathlib import Path

from .browser import looks_like_login, open_context, save_debug_artifacts, visible_text
from .errors import DownloadTimeoutError, EmptyExportError, SessionExpiredError

JD_HOME_URL = "https://shop.jd.com/jdm/home"
JD_ORDER_LIST_URL = "https://shop.jd.com/jdm/trade/orders/order-list?tabType=allOrders"
# Use the passport URL for bootstrap rather than JD_HOME_URL. An anonymous
# visit to the latter briefly keeps the shop URL while its document title says
# "Loading https://passport.../login"; bootstrap-login's generic URL/body
# heuristic would otherwise save an empty context before the login UI paints.
JD_LOGIN_URL = (
    "https://passport.shop.jd.com/login/index.action/jdm?ReturnUrl="
    "https%3A%2F%2Fshop.jd.com%2Fjdm%2Fhome"
)

_LOAD_POLL_MS = 1000
_LOAD_MAX_MS = 30000
_PAGE_CHANGE_MAX_MS = 15000
_MAX_PAGES = 200

JD_CSV_HEADERS = [
    "订单号",
    "商品ID",
    "商品名称",
    "订购数量",
    "下单时间",
    "京东价",
    "订单金额",
    "商家应收",
    "订单状态",
    "下单帐号",
    "客户姓名",
    "客户地址",
    "联系电话",
    # Collector-only helper. The JD normalizer uses it as customer_key when
    # present; official/manual JD files do not have it and retain their
    # existing address-based customer-key semantics.
    "采集客户标识",
]

_CARD_SNAPSHOT_JS = r"""
cards => cards.map(card => {
  const text = node => (node && node.innerText ? node.innerText.trim() : '');
  const header = text(card.querySelector('.card-header'));
  const orderId = text(card.querySelector('.order-info-btn'));
  const orderTimeMatch = header.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}/);
  const columns = Array.from(card.querySelectorAll('.card-content > .card-content-column'));
  const amountMatch = text(columns[1]).match(/¥\s*([\d,.]+)/);
  const buyer = columns[2];
  const combinedAddress = text(buyer && buyer.querySelector('.cons-address-text'));
  const phone = text(buyer && buyer.querySelector('.cons-mobile-phone-text'));
  const userPin = text(buyer && buyer.querySelector('.user-pin'));
  const skus = Array.from(card.querySelectorAll('.sku-info-card')).map(sku => {
    const description = text(sku.querySelector('.card-left__desc'));
    const skuIdMatch = description.match(/skuId\s*[：:]\s*(\d+)/i);
    const values = Array.from(sku.querySelectorAll('.config-view-item__value')).map(text);
    const priceValue = values.find(v => /¥\s*[\d,.]+/.test(v)) || '';
    const priceMatch = priceValue.match(/¥\s*([\d,.]+)/);
    const quantityValue = values.find(v => /^x\s*\d+$/i.test(v)) || '';
    const quantityMatch = quantityValue.match(/x\s*(\d+)/i);
    return {
      name: text(sku.querySelector('.sku-name')),
      skuId: skuIdMatch ? skuIdMatch[1] : '',
      price: priceMatch ? priceMatch[1].replace(/,/g, '') : '',
      quantity: quantityMatch ? Number(quantityMatch[1]) : 1
    };
  });
  return {
    orderId,
    orderTime: orderTimeMatch ? orderTimeMatch[0] : '',
    amount: amountMatch ? amountMatch[1].replace(/,/g, '') : '',
    combinedAddress,
    phone,
    userPin,
    statusText: text(columns[3]),
    skus
  };
})
"""

_ORDER_STATUS_LABELS = (
    "等待境外出库",
    "等待境内发货",
    "待付款",
    "待出库",
    "已发货",
    "暂停订单",
    "锁定订单",
    "已完成",
    "已取消",
)


def _split_receiver_address(value: object) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    parts = re.split(r"[，,]", raw, maxsplit=1)
    return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""


def _clean_status(value: object) -> str:
    raw = str(value or "").strip()
    for label in _ORDER_STATUS_LABELS:
        if label in raw:
            return label
    return raw.splitlines()[0].strip() if raw else ""


def rows_from_card_snapshots(cards: list[dict]) -> list[dict[str, object]]:
    """Convert browser snapshots into one JD-compatible row per SKU.

    Kept browser-independent so parsing can be covered without Chromium and
    so portal selector failures do not get confused with ETL failures.
    """
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for card in cards:
        order_id = str(card.get("orderId") or "").strip()
        order_time = str(card.get("orderTime") or "").strip()
        if not order_id or not order_time:
            continue

        receiver, address = _split_receiver_address(card.get("combinedAddress"))
        phone = str(card.get("phone") or "").strip()
        user_pin = str(card.get("userPin") or "").strip()
        customer_key = user_pin or address or phone
        amount = str(card.get("amount") or "").strip()
        status = _clean_status(card.get("statusText"))
        skus = card.get("skus") or [{}]

        for sku in skus:
            sku_id = str(sku.get("skuId") or "").strip()
            name = str(sku.get("name") or "").strip()
            dedup_key = (order_id, sku_id, name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            price = str(sku.get("price") or "").strip()
            rows.append({
                "订单号": order_id,
                "商品ID": sku_id,
                "商品名称": name,
                "订购数量": sku.get("quantity") or 1,
                "下单时间": order_time,
                "京东价": price,
                "订单金额": amount or price,
                "商家应收": amount or price,
                "订单状态": status,
                "下单帐号": user_pin,
                "客户姓名": receiver,
                "客户地址": address,
                "联系电话": phone,
                "采集客户标识": customer_key,
            })
    return rows


def csv_bytes_from_card_snapshots(cards: list[dict]) -> bytes:
    rows = rows_from_card_snapshots(cards)
    if not rows:
        raise EmptyExportError("JD order list contained no parseable order rows")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=JD_CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _order_page_state(page) -> str:
    """Return ready/expired/timeout after the JD order SPA settles."""
    waited = 0
    while waited < _LOAD_MAX_MS:
        text = visible_text(page)
        if _looks_like_jd_login(page):
            page.wait_for_timeout(_LOAD_POLL_MS)
            waited += _LOAD_POLL_MS
            continue
        if page.locator(".card").count() > 0:
            return "ready"
        if re.search(r"共\s*0\s*条", text) or "暂无订单" in text or "暂无数据" in text:
            return "ready"
        page.wait_for_timeout(_LOAD_POLL_MS)
        waited += _LOAD_POLL_MS
    return "expired" if _looks_like_jd_login(page) else "timeout"


def _looks_like_jd_login(page) -> bool:
    """JD may retain the requested shop URL while its title already points
    at passport/login during the redirect; inspect both visible signals."""
    try:
        title = page.title().lower()
    except Exception:
        title = ""
    return looks_like_login(page.url, visible_text(page)) or "passport.shop.jd.com/login" in title


def _goto_order_page(page) -> str:
    page.goto(JD_ORDER_LIST_URL, wait_until="domcontentloaded")
    return _order_page_state(page)


def verify_jd_session(storage_path: Path, *, headless: bool | None = None) -> bool:
    """Read-only check that the saved 京麦 session can render the order list."""
    with open_context(storage_path, headless=headless) as page:
        state = _goto_order_page(page)
        if state == "timeout":
            raise DownloadTimeoutError("JD order list did not finish loading during session verification")
        return state == "ready"


def _scrape_all_pages(page) -> list[dict]:
    snapshots: list[dict] = []
    visited_first_ids: set[str] = set()

    for _page_number in range(1, _MAX_PAGES + 1):
        cards = page.locator(".card").evaluate_all(_CARD_SNAPSHOT_JS)
        if not cards:
            break
        first_id = str(cards[0].get("orderId") or "")
        if first_id in visited_first_ids:
            raise DownloadTimeoutError(f"JD pagination repeated order {first_id!r}; refusing a partial import")
        visited_first_ids.add(first_id)
        snapshots.extend(cards)

        next_button = page.get_by_role("button", name="下一页", exact=True)
        if next_button.count() == 0 or not next_button.first.is_enabled():
            return snapshots

        next_button.first.click()
        waited = 0
        while waited < _PAGE_CHANGE_MAX_MS:
            page.wait_for_timeout(_LOAD_POLL_MS)
            if _looks_like_jd_login(page):
                raise SessionExpiredError("JD session expired while paging through orders")
            current = page.locator(".card .order-info-btn")
            if current.count() > 0 and current.first.inner_text().strip() != first_id:
                break
            waited += _LOAD_POLL_MS
        else:
            raise DownloadTimeoutError(f"JD order list did not advance after order {first_id!r}")
    else:
        raise DownloadTimeoutError(f"JD order list exceeded safety limit of {_MAX_PAGES} pages")

    return snapshots


def collect_jd(storage_path: Path, *, headless: bool | None = None) -> tuple[bytes, str]:
    """Collect the visible six-month JD order list as a JD-compatible CSV."""
    with open_context(storage_path, headless=headless) as page:
        state = _goto_order_page(page)
        if state == "expired":
            save_debug_artifacts(page, "jd_session_expired")
            raise SessionExpiredError(f"JD session expired (redirected to {page.url!r})")
        if state == "timeout":
            save_debug_artifacts(page, "jd_order_list_timeout")
            raise DownloadTimeoutError("JD order list did not finish loading")

        try:
            data = csv_bytes_from_card_snapshots(_scrape_all_pages(page))
        except (SessionExpiredError, DownloadTimeoutError, EmptyExportError):
            save_debug_artifacts(page, "jd_collect_failed")
            raise
        except Exception as exc:
            save_debug_artifacts(page, "jd_collect_failed")
            raise DownloadTimeoutError(f"JD order-list DOM could not be collected: {exc}") from exc

        filename = f"jd_orders_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        return data, filename
