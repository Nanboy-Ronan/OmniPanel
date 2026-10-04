from __future__ import annotations
import io
import streamlit as st
import pandas as pd
from app.db.etl.detect import detect_platform

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
        cols[2].metric("已保存来源行", raw_rows)
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
            st.session_state["active_upload_batch"] = batch_id
            st.success(f"文件已接收，批次 #{batch_id} 正在后台处理。可离开此页，稍后在上传记录查看结果。")
            return

        clear_cached_orders()
        st.session_state.pop("sidebar_upload_summary", None)
        st.session_state.pop("data_freshness_cache", None)
        detected = data.get("platform", "unknown")
        _render_upload_summary(data, detected, expected_platform, label)
        _render_rejected_rows(data.get("batch_id"), data.get("invalid_rows", 0))

    def _upload_card(label: str, platform: str, key: str):
        # Keep the file widget outside the form so selecting a file immediately
        # reruns the page and shows the preview before the user submits it.
        f = st.file_uploader(f"{label} 订单导出文件", type=["csv", "xlsx"], key=key)
        mismatch = False
        if f is not None:
            try:
                sample = (pd.read_excel(io.BytesIO(f.getvalue()), nrows=5, dtype=str)
                          if f.name.lower().endswith(".xlsx")
                          else pd.read_csv(io.BytesIO(f.getvalue()), nrows=5, dtype=str))
                st.caption(f"文件预览：{len(sample.columns)} 列；以下为前 {len(sample)} 行。请确认平台和列名。")
                st.dataframe(sample, use_container_width=True, hide_index=True)
                try:
                    detected = detect_platform(sample)
                except ValueError:
                    st.warning("暂时无法根据列名识别平台，请检查文件是否为原始订单导出文件。")
                else:
                    if detected != platform:
                        st.error(f"检测到文件来自 {detected}，请切换到对应页签后上传。")
                        mismatch = True
            except Exception:
                st.warning("无法预览文件；提交后系统会继续检查格式并给出处理结果。")
        with st.form(f"upload-{key}", clear_on_submit=False):
            submitted = st.form_submit_button("上传", disabled=f is None or mismatch)
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

    active_id = st.session_state.get("active_upload_batch")
    if active_id:
        st.markdown(f"#### 最近提交：批次 #{active_id}")
        if st.button("刷新处理状态"):
            st.rerun()
        try:
            active_response = client.upload_batch(active_id)
        except Exception:
            st.warning("暂时无法查询处理状态，请稍后重试。")
        else:
            if active_response.status_code == 200:
                active = active_response.json()
                status = active.get("status")
                if status in {"processing", "recovering"}:
                    st.info("正在处理文件。关闭此页不会中断处理。")
                elif status == "failed":
                    st.error(f"处理失败：{active.get('error_message') or '请检查文件格式后重试。'}")
                else:
                    active.setdefault("batch_id", active.get("id", active_id))
                    active.setdefault("total_rows", active.get("row_count"))
                    _render_upload_summary(active, active.get("platform", "unknown"),
                                           active.get("platform", "unknown"), "当前平台")
                    _render_rejected_rows(active_id, active.get("invalid_rows", 0))
                    if st.button("查看导入数据"):
                        clear_cached_orders()
                        st.session_state.pop("sidebar_upload_summary", None)
                        st.session_state.pop("data_freshness_cache", None)
                        st.session_state["page"] = "数据浏览"
                        st.rerun()
            else:
                show_api_error(active_response, "无法查询处理状态。")

    # ── Upload history ────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("最近上传记录", expanded=True):
        r_hist = client.upload_batches(limit=20)
        if r_hist.status_code == 200:
            batches = r_hist.json()
            if batches:
                chosen_id = st.selectbox(
                    "查看历史批次详情",
                    [b["id"] for b in batches],
                    format_func=lambda batch: next(
                        (f"#{b['id']} · {b['filename']} · {b['status']}" for b in batches if b["id"] == batch),
                        str(batch),
                    ),
                )
                if st.button("查看该批次"):
                    st.session_state["active_upload_batch"] = chosen_id
                    st.rerun()
                hist_df = pd.DataFrame(batches)
                hist_df["uploaded_at"] = pd.to_datetime(hist_df["uploaded_at"])
                hist_df = hist_df.rename(columns={
                    "id": "批次",
                    "status": "状态",
                    "platform": "平台",
                    "filename": "文件名",
                    "inserted_orders": "新增",
                    "duplicate_rows": "重复",
                    "invalid_rows": "拒绝",
                    "uploaded_at": "上传时间",
                })
                status_labels = {"processing": "处理中", "recovering": "恢复处理中", "failed": "失败", "completed": "完成"}
                hist_df["状态"] = hist_df["状态"].map(status_labels).fillna(hist_df["状态"])
                show_cols = ["批次", "状态", "平台", "文件名", "新增", "重复", "拒绝", "上传时间"]
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
