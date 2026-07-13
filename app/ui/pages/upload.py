from __future__ import annotations
import io
import time
import streamlit as st
import pandas as pd

from app.ui._helpers import _page_hero, show_api_error, clear_cached_orders


def page_upload() -> None:
    client = st.session_state["client"]
    _page_hero("数据上传")

    def _render_upload_summary(data: dict, detected: str, expected_platform: str, label: str) -> None:
        inserted = data.get("inserted_orders", data.get("inserted_rows", 0))
        raw_rows = data.get("raw_rows_inserted", inserted)
        duplicates = data.get("duplicate_rows", 0)
        invalid = data.get("invalid_rows", 0)
        total = data.get("total_rows")
        batch_id = data.get("batch_id")

        if detected != expected_platform and detected != "unknown":
            st.warning(f"检测到文件来自 **{detected}**，但上传至 **{label}** 页签。")
        elif invalid:
            st.warning("上传完成，但存在被拒绝的行。")
        elif duplicates and not inserted:
            st.info("上传完成，所有有效行均已存在，未新增数据。")
        else:
            st.success("上传完成。")

        if batch_id is not None:
            st.caption(f"批次 #{batch_id} · 平台：{detected}")
        else:
            st.caption(f"平台：{detected}")

        cols = st.columns(5)
        cols[0].metric("来源行数", total if total is not None else "N/A")
        cols[1].metric("新增订单", inserted)
        cols[2].metric("原始行已存", raw_rows)
        cols[3].metric("重复行", duplicates)
        cols[4].metric("拒绝行", invalid)

    def _render_rejected_rows(batch_id: int | None, invalid_count: int) -> None:
        if not batch_id or not invalid_count:
            return
        with st.expander(f"查看被拒绝行（{invalid_count} 行）", expanded=True):
            r = client.upload_batch_rejected(batch_id)
            if r.status_code != 200:
                st.caption("无法加载被拒绝行详情。")
                return
            body = r.json()
            rows = body.get("rows", [])
            if not rows:
                st.caption("无被拒绝行。")
                return

            records = [
                {
                    "行号": row["source_row_number"],
                    "拒绝原因": row["reason"],
                    **{k: v for k, v in (row.get("raw_payload") or {}).items()},
                }
                for row in rows
            ]
            df = pd.DataFrame(records)
            st.dataframe(df, use_container_width=True, hide_index=True)

            csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="下载被拒绝行 CSV",
                data=io.BytesIO(csv_bytes),
                file_name=f"rejected_rows_batch_{batch_id}.csv",
                mime="text/csv",
            )

    def _process_upload(f, expected_platform: str, label: str):
        if not f:
            st.warning("请先选择文件。")
            return

        with st.spinner(f"正在上传 {label} 文件…"):
            r = client.upload(f.name, f.getvalue(), expected_platform=expected_platform)

        if r.status_code >= 400:
            show_api_error(r, "上传失败。")
            return

        resp = r.json()
        batch_id = resp.get("batch_id")
        if batch_id is None:
            # Legacy synchronous response (should not happen in normal operation)
            data = resp
        else:
            # Poll until the background ETL finishes (up to ~120 s), backing off
            # from 1 s to 5 s between checks — most uploads finish in the first
            # couple of checks, so this avoids hammering the API once a large
            # file's ETL genuinely takes a while.
            data = None
            with st.spinner("正在处理文件，请稍候…"):
                elapsed = 0.0
                delay = 1.0
                while elapsed < 120:
                    r_batch = client.upload_batch(batch_id)
                    if r_batch.status_code == 200:
                        batch_data = r_batch.json()
                        if batch_data.get("status") != "processing":
                            data = batch_data
                            break
                    time.sleep(delay)
                    elapsed += delay
                    delay = min(delay * 2, 5.0)

            if data is None:
                st.warning("处理超时，请稍后在上传记录中查看结果。")
                return

            if data.get("status") == "failed":
                st.error(f"文件处理失败：{data.get('error_message') or '未知错误'}")
                return

            # Normalise key names so _render_upload_summary works unchanged
            # (the batch-status endpoint uses id/row_count/inserted_orders, while
            # the legacy synchronous ingest_upload() path uses
            # batch_id/total_rows/inserted_rows). Without this, batch_id stays
            # unset and the rejected-rows expander never renders, even when
            # rows were actually rejected.
            data.setdefault("batch_id", data.get("id", batch_id))
            data.setdefault("inserted_rows", data.get("inserted_orders", 0))
            data.setdefault("total_rows", data.get("row_count"))

        clear_cached_orders()
        detected = data.get("platform", "unknown")
        _render_upload_summary(data, detected, expected_platform, label)
        _render_rejected_rows(data.get("batch_id"), data.get("invalid_rows", 0))

    def _upload_card(label: str, platform: str, key: str):
        with st.form(f"upload-{key}", clear_on_submit=False):
            f = st.file_uploader(f"{label} 订单导出文件", type=["csv", "xlsx"], key=key)
            submitted = st.form_submit_button("上传")
            if submitted:
                _process_upload(f, platform, label)

    tab_yz, tab_jd, tab_tm = st.tabs(["有赞", "京东", "天猫"])

    with tab_yz:
        st.caption(
            "上传**有赞订单导出**文件（.csv 或 .xlsx）。"
            "必须包含：订单号、订单创建时间、买家手机号、收货人省份、订单实付金额、全部商品名称。"
        )
        _upload_card("有赞", "youzan", "yz")

    with tab_jd:
        st.caption(
            "上传**京东订单导出**文件（.csv 或 .xlsx）。"
            "必须包含：订单号、下单时间、商品名称、客户姓名、客户地址、商家应收。"
        )
        _upload_card("京东", "jd", "jd")

    with tab_tm:
        st.caption(
            "上传**天猫订单导出**文件（.csv 或 .xlsx）。"
            "必须包含：订单编号、订单创建时间、收货地址、买家应付货款、商品标题。"
        )
        _upload_card("天猫", "tmall", "tm")

    # ── Upload history ────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("最近上传记录", expanded=False):
        r_hist = client.upload_batches(limit=10)
        if r_hist.status_code == 200:
            batches = r_hist.json()
            if batches:
                hist_df = pd.DataFrame(batches)
                hist_df["uploaded_at"] = pd.to_datetime(hist_df["uploaded_at"])
                hist_df = hist_df.rename(columns={
                    "id": "批次",
                    "platform": "平台",
                    "filename": "文件名",
                    "inserted_orders": "新增",
                    "duplicate_rows": "重复",
                    "invalid_rows": "拒绝",
                    "uploaded_at": "上传时间",
                })
                show_cols = ["批次", "平台", "文件名", "新增", "重复", "拒绝", "上传时间"]
                st.dataframe(
                    hist_df[[c for c in show_cols if c in hist_df.columns]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "上传时间": st.column_config.DatetimeColumn(
                            "上传时间", format="MM-DD HH:mm"
                        ),
                    },
                )
            else:
                st.info("暂无上传记录。")
        else:
            st.caption("无法加载上传记录。")
