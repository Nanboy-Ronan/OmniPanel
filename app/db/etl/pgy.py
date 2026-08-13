from __future__ import annotations

import json
from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..models import PgyNote

_DEDUP_CONSTRAINT = "uq_pgy_notes_account_note_id"

_UPSERT_UPDATE_COLS = [
    "blogger_nickname", "blogger_url", "blogger_fans", "blogger_health",
    "note_title", "note_url", "note_type", "publish_date", "note_source", "content_tag",
    "order_id", "cooperation_name", "report_brand", "order_account", "blogger_quote", 
    "service_fee", "is_premium_mode", "spu_name", "impressions", "reads", "read_uv",
    "play_rate_5s", "read_rate_3s", "video_duration", "avg_view_duration", "video_completion_rate",
    "interactions", "interaction_rate", "likes", "collects", "comments", "shares", "follows",
    "organic_impressions", "organic_reads", "paid_impressions", "paid_reads",
    "boosted_impressions", "boosted_reads", "cost_per_read", "cost_per_interaction",
    "fan_ratio", "female_ratio", "male_ratio", "audience_json", "component_json", "data_date"
]

def _parse_date(raw) -> Optional[date]:
    if not raw or pd.isna(raw) or str(raw).strip() == '-':
        return None
    raw_str = str(raw).strip()
    try:
        if '/' in raw_str:
            parts = raw_str.split('/')
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        elif '-' in raw_str:
            parts = raw_str.split('-')
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        pass
    return None

def _int_or_none(v) -> Optional[int]:
    if pd.isna(v) or str(v).strip() == '-':
        return None
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return None

def _float_or_none(v) -> Optional[float]:
    if pd.isna(v) or str(v).strip() == '-':
        return None
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None

def _str_or_none(v) -> Optional[str]:
    if pd.isna(v) or str(v).strip() == '-':
        return None
    s = str(v).strip()
    return s if s else None

def parse_pgy_xlsx(df_raw: pd.DataFrame) -> list[dict]:
    # Skip rows 1-2 (0-1 in 0-indexed) which are merged header groups
    # Row 3 (index 2) is the actual column names
    if len(df_raw) < 3:
        return []
        
    df = df_raw.iloc[3:].copy()
    df = df.reset_index(drop=True)

    rows = []
    for _, row in df.iterrows():
        note_id = _str_or_none(row.iloc[10])
        if not note_id:
            continue
            
        audience_json = {
            "age": {
                "<18": _str_or_none(row.iloc[81]),
                "18~24": _str_or_none(row.iloc[82]),
                "25~34": _str_or_none(row.iloc[83]),
                "35~44": _str_or_none(row.iloc[84]),
                ">44": _str_or_none(row.iloc[85])
            },
            "device_top3": [
                {"name": _str_or_none(row.iloc[86]), "ratio": _str_or_none(row.iloc[87])},
                {"name": _str_or_none(row.iloc[88]), "ratio": _str_or_none(row.iloc[89])},
                {"name": _str_or_none(row.iloc[90]), "ratio": _str_or_none(row.iloc[91])}
            ],
            "region_top3": [
                {"name": _str_or_none(row.iloc[92]), "ratio": _str_or_none(row.iloc[93])},
                {"name": _str_or_none(row.iloc[94]), "ratio": _str_or_none(row.iloc[95])},
                {"name": _str_or_none(row.iloc[96]), "ratio": _str_or_none(row.iloc[97])}
            ],
            "interest_top3": [
                {"name": _str_or_none(row.iloc[98]), "ratio": _str_or_none(row.iloc[99])},
                {"name": _str_or_none(row.iloc[100]), "ratio": _str_or_none(row.iloc[101])},
                {"name": _str_or_none(row.iloc[102]), "ratio": _str_or_none(row.iloc[103])}
            ]
        }
        
        component_json = {
            "text_component": {
                "type": _str_or_none(row.iloc[55]), "text": _str_or_none(row.iloc[56]),
                "impressions": _str_or_none(row.iloc[57]), "clicks": _str_or_none(row.iloc[58]),
                "click_users": _str_or_none(row.iloc[59]), "ctr": _str_or_none(row.iloc[60])
            },
            "bottom_bar": {
                "type": _str_or_none(row.iloc[61]), "text": _str_or_none(row.iloc[62]),
                "impressions": _str_or_none(row.iloc[63]), "clicks": _str_or_none(row.iloc[64]),
                "click_users": _str_or_none(row.iloc[65]), "ctr": _str_or_none(row.iloc[66])
            },
            "interactive": {
                "type": _str_or_none(row.iloc[67]), "title": _str_or_none(row.iloc[68]),
                "impression_users": _str_or_none(row.iloc[69]), "participants": _str_or_none(row.iloc[70]),
                "participation_rate": _str_or_none(row.iloc[71])
            },
            "comment_area": {
                "type": _str_or_none(row.iloc[72]), "text": _str_or_none(row.iloc[73]),
                "impressions": _str_or_none(row.iloc[74]), "clicks": _str_or_none(row.iloc[75]),
                "click_users": _str_or_none(row.iloc[76]), "ctr": _str_or_none(row.iloc[77])
            }
        }

        rows.append({
            "data_date": _parse_date(row.iloc[0]),
            "blogger_nickname": _str_or_none(row.iloc[1]),
            "blogger_url": _str_or_none(row.iloc[2]),
            "blogger_fans": _int_or_none(row.iloc[3]),
            "blogger_health": _str_or_none(row.iloc[4]),
            "note_title": _str_or_none(row.iloc[5]),
            "note_url": _str_or_none(row.iloc[6]),
            "note_type": _str_or_none(row.iloc[7]),
            "publish_date": _parse_date(row.iloc[8]),
            "note_source": _str_or_none(row.iloc[9]),
            "note_id": note_id,
            "content_tag": _str_or_none(row.iloc[11]),
            "order_id": _str_or_none(row.iloc[12]),
            "cooperation_name": _str_or_none(row.iloc[13]),
            "report_brand": _str_or_none(row.iloc[14]),
            "order_account": _str_or_none(row.iloc[15]),
            "blogger_quote": _float_or_none(row.iloc[16]),
            "service_fee": _float_or_none(row.iloc[17]),
            "is_premium_mode": _str_or_none(row.iloc[18]),
            "spu_name": _str_or_none(row.iloc[19]),
            "impressions": _int_or_none(row.iloc[20]),
            "reads": _int_or_none(row.iloc[21]),
            "read_uv": _int_or_none(row.iloc[22]),
            "play_rate_5s": _str_or_none(row.iloc[23]),
            "read_rate_3s": _str_or_none(row.iloc[24]),
            "video_duration": _float_or_none(row.iloc[25]),
            "avg_view_duration": _float_or_none(row.iloc[26]),
            "video_completion_rate": _str_or_none(row.iloc[27]),
            "interactions": _int_or_none(row.iloc[28]),
            "interaction_rate": _str_or_none(row.iloc[29]),
            "likes": _int_or_none(row.iloc[30]),
            "collects": _int_or_none(row.iloc[31]),
            "comments": _int_or_none(row.iloc[32]),
            "shares": _int_or_none(row.iloc[33]),
            "follows": _int_or_none(row.iloc[34]),
            "organic_impressions": _int_or_none(row.iloc[35]),
            "organic_reads": _int_or_none(row.iloc[36]),
            "paid_impressions": _int_or_none(row.iloc[37]),
            "paid_reads": _int_or_none(row.iloc[38]),
            "boosted_impressions": _int_or_none(row.iloc[39]),
            "boosted_reads": _int_or_none(row.iloc[40]),
            "cost_per_read": _float_or_none(row.iloc[53]),
            "cost_per_interaction": _float_or_none(row.iloc[54]),
            "fan_ratio": _str_or_none(row.iloc[78]),
            "female_ratio": _str_or_none(row.iloc[79]),
            "male_ratio": _str_or_none(row.iloc[80]),
            "audience_json": json.dumps(audience_json, ensure_ascii=False),
            "component_json": json.dumps(component_json, ensure_ascii=False),
        })
    return rows

def upsert_pgy_notes(rows: list[dict], account_id: int, session: Session) -> dict:
    if not rows:
        return {"total": 0, "upserted": 0}

    rows_with_account = [{**r, "account_id": account_id} for r in rows]

    stmt = (
        pg_insert(PgyNote)
        .values(rows_with_account)
        .on_conflict_do_update(
            constraint=_DEDUP_CONSTRAINT,
            set_={col: pg_insert(PgyNote).excluded[col] for col in _UPSERT_UPDATE_COLS}
            | {"updated_at": text("NOW()")},
        )
    )
    session.execute(stmt)
    session.commit()
    return {"total": len(rows), "upserted": len(rows)}
