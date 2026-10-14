"""Bounded, explicitly projected business data for the enterprise connector.

No ORM serialization, raw payloads, credentials or customer records cross this boundary.
Account IDs select business accounts; they never select an authorization identity.
"""
from datetime import date, timedelta
from sqlalchemy import select, func
from mcp.server.fastmcp.exceptions import ToolError
from ...db.models import (MediaAccount, MediaPost, MediaPostMetricDaily, XhsAccount,
    XhsPost, XhsAccountDailyMetric, XhsAudienceSourceDaily, WxChannelsAccount,
    WxChannelsPost, ZhihuPost, PgyNote)

ACCOUNTS = {"wechat": MediaAccount, "xhs": XhsAccount, "channels": WxChannelsAccount, "pgy": XhsAccount}
POSTS = {
    "wechat": (MediaPost, "id account_id title url publish_date"),
    "xhs": (XhsPost, "id account_id title publish_date genre impressions views cover_click_rate likes comments collects shares new_followers avg_watch_time updated_at"),
    "channels": (WxChannelsPost, "id account_id title publish_date plays recommends likes_thumb comments shares new_fans forwards_chat_moments avg_watch_duration completion_rate wecom_link_clicks wecom_link_click_users added_to_contacts added_to_contacts_users updated_at"),
    "zhihu": (ZhihuPost, "id content_type title url publish_date reads plays likes favorites comments collects shares updated_at"),
    "pgy": (PgyNote, "id account_id note_title note_url publish_date note_type impressions reads read_uv interactions likes collects comments shares follows organic_impressions organic_reads paid_impressions paid_reads boosted_impressions boosted_reads avg_view_duration video_completion_rate"),
}
WECHAT_FIELDS = "read_count read_user_count share_count share_user_count like_user comment_count collection_user add_to_fav_count read_avg_time read_finish_rate zaikan_user read_subscribe_user read_delivery_rate"
DAILY_FIELDS = "rise_fans_count loss_fans_count net_rise_fans_count view_count view_time_total_seconds avg_view_time_seconds home_view_count like_count collect_count comment_count share_count danmaku_count cover_click_rate video_full_view_rate"
ADDITIVE = "rise_fans_count loss_fans_count net_rise_fans_count view_count view_time_total_seconds home_view_count like_count collect_count comment_count share_count danmaku_count".split()
COUNTERS = "read_count read_user_count share_count share_user_count like_user comment_count collection_user".split()


def columns(model, fields):
    return [getattr(model, field) for field in fields.split()]


async def account_ids(session, source, account_id=None):
    if source == "zhihu":
        if account_id is not None:
            raise ToolError("知乎为平台单账号数据，请省略 account_id。")
        return None
    model = ACCOUNTS[source]
    stmt = select(model.id).where(model.is_active.is_(True))
    if source == "wechat":
        stmt = stmt.where(model.platform == "wechat_official")
    if account_id is not None:
        stmt = stmt.where(model.id == account_id)
    ids = list((await session.execute(stmt)).scalars())
    if account_id is not None and not ids:
        raise ToolError("账号不存在或已停用，请先查询可用账号列表。")
    return ids


async def accounts(session, source):
    result = []
    for src in ([source] if source else list(POSTS)):
        ids = await account_ids(session, src)
        if src == "zhihu":
            result.append({"source": src, "account_id": None, "name": "知乎（平台单账号）"})
        else:
            model = ACCOUNTS[src]
            rows = (await session.execute(select(model.id, model.name).where(model.id.in_(ids)).order_by(model.id))).all()
            result.extend({"source": src, "account_id": r.id, "name": r.name} for r in rows)
    return {"data": result, "permission": "平台管理员/分析员 + analytics:media；可查询启用的公司媒体账号。采集负责人仅约束登录修复，不约束业务数据读取。"}


async def snapshots(session, post_ids, end):
    stmt = (select(MediaPostMetricDaily.post_id, MediaPostMetricDaily.metric_date, MediaPostMetricDaily.read_user_source,
                   *columns(MediaPostMetricDaily, WECHAT_FIELDS))
            .where(MediaPostMetricDaily.post_id.in_(post_ids), MediaPostMetricDaily.metric_date <= end)
            .distinct(MediaPostMetricDaily.post_id)
            .order_by(MediaPostMetricDaily.post_id, MediaPostMetricDaily.metric_date.desc()))
    result = {}
    for row in (await session.execute(stmt)).mappings():
        item = dict(row)
        raw = item.pop("read_user_source")
        item["read_sources"] = [
            {"scene": str(v.get("scene_desc", "未知"))[:80], "user_count": v.get("user_count")}
            for v in (raw if isinstance(raw, list) else [])[:50]
            if isinstance(v, dict) and isinstance(v.get("user_count"), (int, float))
        ]
        result[item["post_id"]] = item
    return result


async def posts(session, source, start, end, account_id, offset, limit):
    ids = await account_ids(session, source, account_id)
    model, fields = POSTS[source]
    filters = [model.publish_date.between(start, end)]
    if ids is not None:
        filters.append(model.account_id.in_(ids))
    total = (await session.execute(select(func.count()).select_from(model).where(*filters))).scalar_one()
    rows = [dict(r) for r in (await session.execute(select(*columns(model, fields)).where(*filters)
            .order_by(model.publish_date.desc(), model.id).offset(offset).limit(limit))).mappings()]
    if source == "wechat":
        latest = await snapshots(session, [r["id"] for r in rows], end)
        for row in rows:
            row["snapshot"] = latest.get(row["id"])
    return {"data": rows, "total": total, "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "definition": "按发布日期筛选内容；公众号附截至期末的最近累计快照，其他渠道为最近采集累计值。不是期间新增，不作不同发布批次的周环比。空列表不代表无业务。空字段保持null。", "period": {"start": start, "end": end}}


async def daily(session, start, end, account_id):
    ids = await account_ids(session, "xhs", account_id)
    model = XhsAccountDailyMetric
    rows = [dict(r) for r in (await session.execute(select(model.account_id, model.metric_date, *columns(model, DAILY_FIELDS))
            .where(model.account_id.in_(ids), model.metric_date.between(start, end))
            .order_by(model.account_id, model.metric_date))).mappings()]
    summaries = []
    expected = (end - start).days + 1
    for aid in ids:
        subset = [r for r in rows if r["account_id"] == aid]
        complete = len(subset) == expected
        sums = {f: sum(r[f] for r in subset) if complete and all(r[f] is not None for r in subset) else None for f in ADDITIVE}
        summaries.append({"account_id": aid, "days_present": len(subset), "days_expected": expected,
                          "complete_dates": complete, "totals": sums})
    return {"data": rows, "accounts": summaries, "definition": "小红书账号逐日指标，可汇总完整期间的新增/流失/净增。缺日或空值对应合计为null；时长单位秒，比例单位百分数(0–100)。比率不直接求和或平均。账号指标不同于笔记累计指标。"}


async def sources(session, account_id, as_of, window):
    ids = await account_ids(session, "xhs", account_id)
    model = XhsAudienceSourceDaily
    latest = (await session.execute(select(func.max(model.snapshot_date)).where(model.account_id.in_(ids),
                model.snapshot_date <= as_of, model.window_label == window))).scalar_one()
    rows = []
    if latest:
        rows = [dict(r) for r in (await session.execute(select(model.snapshot_date, model.window_label,
                model.source_type, model.title, model.value_pct).where(model.account_id.in_(ids),
                model.snapshot_date == latest, model.window_label == window).order_by(model.source_type))).mappings()]
    return {"data": rows, "snapshot_date": latest, "requested_as_of": as_of,
            "definition": "采集日所见平台7/30日窗口的来源占比快照，不是单日增量；不可相加，不能冒充其他日期窗口。不返回指定日期之后的快照。"}


async def wechat_period(session, start, end, account_id, offset, limit):
    ids = await account_ids(session, "wechat", account_id)
    active = select(MediaPostMetricDaily.post_id).where(MediaPostMetricDaily.metric_date.between(start, end))
    filters = [MediaPost.account_id.in_(ids), MediaPost.publish_date <= end,
               (MediaPost.id.in_(active) | (MediaPost.publish_date >= start))]
    total = (await session.execute(select(func.count()).select_from(MediaPost).where(*filters))).scalar_one()
    rows = [dict(r) for r in (await session.execute(select(MediaPost.id, MediaPost.account_id, MediaPost.title,
            MediaPost.url, MediaPost.publish_date).where(*filters).order_by(MediaPost.publish_date.desc(), MediaPost.id)
            .offset(offset).limit(limit))).mappings()]
    pids = [r["id"] for r in rows]
    baseline_day = start - timedelta(days=1)
    baseline = await snapshots(session, pids, baseline_day)
    ending = await snapshots(session, pids, end)
    for r in rows:
        b, e = baseline.get(r["id"]), ending.get(r["id"])
        is_new = start <= r["publish_date"] <= end
        exact = bool(e and e["metric_date"] == end and (is_new or (b and b["metric_date"] == baseline_day)))
        delta = {}
        for f in COUNTERS:
            bv = 0 if is_new else (b.get(f) if b else None)
            ev = e.get(f) if e else None
            delta[f] = ev-bv if exact and bv is not None and ev is not None and ev >= bv else None
        r.update(is_new_in_period=is_new, baseline= b, end_snapshot=e,
                 complete=exact and all(v is not None for v in delta.values()), period_delta=delta)
    return {"data": rows, "total": total, "next_offset": offset+len(rows) if offset+len(rows)<total else None,
            "period": {"start": start, "end": end},
            "definition": "公众号按文章期末累计减期初累计计算区间增量，新文章期初为0。缺精确边界快照、空指标、累计回退则对应增量为null。仅本页明细，取完所有页后才可汇总；不跨文章去重。阅读时长/完读率为期末累计口径，非本周平均。read_subscribe_user是关注者阅读人数，不是新增关注。账号粉丝变化不由文章数据推导。"}
