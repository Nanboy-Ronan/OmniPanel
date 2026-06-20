"""app/ui/pages/media_upload.py — 公众号数据手动上传页面

DISABLED 2026-06-01: xlsx upload was never used in production; data comes from
manual API sync (POST /media/wechat/sync).  The page body is commented out.
See README §"Disabled: WeChat xlsx upload" to re-enable.
"""
from __future__ import annotations

# import pandas as pd  # disabled xlsx upload
# import streamlit as st  # disabled xlsx upload
# from app.ui._helpers import _page_hero, show_api_error  # disabled xlsx upload


def page_media_upload() -> None:
    pass  # disabled — xlsx upload removed 2026-06-01, see README


# def page_media_upload() -> None:  # disabled xlsx upload 2026-06-01
#     client = st.session_state["client"]
#     is_admin = st.session_state.get("is_admin", False)
#     _page_hero("公众号上传")
#     r_accounts = client.media_accounts()
#     if r_accounts.status_code != 200:
#         show_api_error(r_accounts, "无法加载公众号账号列表。")
#         return
#     accounts: list[dict] = r_accounts.json()
#     if is_admin:
#         with st.expander("➕ 新建公众号账号", expanded=False):
#             with st.form("create_media_account"):
#                 new_name = st.text_input("账号名称", placeholder="例：示例公众号")
#                 new_app_id = st.text_input("AppID（可选）", placeholder="wx...")
#                 if st.form_submit_button("创建账号", use_container_width=True):
#                     if not new_name.strip():
#                         st.error("账号名称不能为空。")
#                     else:
#                         r = client.media_create_account(new_name.strip(), new_app_id.strip() or None)
#                         if r.status_code == 201:
#                             st.success(f"账号「{r.json()['name']}」已创建。")
#                             st.rerun()
#                         else:
#                             show_api_error(r, "创建账号失败。")
#     if not accounts:
#         st.info("尚未创建任何公众号账号。请管理员先点击上方「➕ 新建公众号账号」。")
#         return
#     st.markdown("---")
#     with st.form("media_upload_form", clear_on_submit=True):
#         account_labels = {a["name"]: a["id"] for a in accounts}
#         selected_name = st.selectbox("公众号账号", options=list(account_labels.keys()))
#         uploaded_file = st.file_uploader("微信后台数据导出文件（.xlsx）", type=["xlsx", "xls"])
#         submitted = st.form_submit_button("上传", use_container_width=True)
#     if submitted:
#         if not uploaded_file:
#             st.warning("请先选择文件。")
#         else:
#             account_id = account_labels[selected_name]
#             with st.spinner(f"正在解析并导入「{selected_name}」的数据…"):
#                 r = client.media_upload(uploaded_file.name, uploaded_file.getvalue(), account_id)
#             if r.status_code != 200:
#                 show_api_error(r, "上传失败。")
#             else:
#                 data = r.json()
#                 inserted = data.get("inserted", 0)
#                 updated  = data.get("updated", 0)
#                 rejected = data.get("rejected", 0)
#                 if rejected and not (inserted + updated):
#                     st.warning(f"上传完成，但所有 {rejected} 行均被拒绝，未导入任何数据。")
#                 elif rejected:
#                     st.warning(f"上传完成，{rejected} 行因数据问题被跳过。")
#                 else:
#                     st.success("上传成功。")
#                 col1, col2, col3 = st.columns(3)
#                 col1.metric("新增文章", inserted)
#                 col2.metric("更新文章", updated)
#                 col3.metric("拒绝行", rejected)
#                 if data.get("rejected_reasons"):
#                     with st.expander(f"被拒绝的行（共 {rejected} 条）", expanded=False):
#                         for reason in data["rejected_reasons"]:
#                             st.caption(reason)
#     st.markdown("---")
#     with st.expander("最近上传记录", expanded=False):
#         r_hist = client.media_uploads(limit=15)
#         if r_hist.status_code != 200:
#             st.caption("无法加载上传记录。")
#         else:
#             runs = r_hist.json()
#             if not runs:
#                 st.info("暂无上传记录。")
#             else:
#                 hist_df = pd.DataFrame(runs)
#                 hist_df["started_at"] = pd.to_datetime(hist_df["started_at"])
#                 rename = {
#                     "account_name": "账号", "filename": "文件名",
#                     "posts_upserted": "新增/更新", "metrics_upserted": "指标行",
#                     "rejected": "拒绝行", "started_at": "上传时间", "status": "状态",
#                 }
#                 show_cols = [c for c in rename if c in hist_df.columns]
#                 st.dataframe(
#                     hist_df[show_cols].rename(columns=rename),
#                     use_container_width=True, hide_index=True,
#                     column_config={"上传时间": st.column_config.DatetimeColumn("上传时间", format="MM-DD HH:mm")},
#                 )
