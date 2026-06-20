from __future__ import annotations
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error


def page_user_management() -> None:
    client = st.session_state["client"]
    _page_hero("用户管理")
    roles = ["viewer", "analyst", "admin"]

    with st.expander("创建新用户", expanded=False):
        with st.form("create-user"):
            c1, c2, c3 = st.columns([3, 2, 1])
            email = c1.text_input("邮箱")
            pwd = c2.text_input("密码", type="password")
            role_new = c3.selectbox("角色", roles)
            if st.form_submit_button("创建用户"):
                if not email or not pwd:
                    st.warning("邮箱和密码为必填项。")
                else:
                    r_new = client.create_user(email, pwd, role_new)
                    if r_new.status_code == 201:
                        st.success(f"用户已创建：{email}")
                        st.rerun()
                    else:
                        show_api_error(r_new)

    st.subheader("现有用户")
    r = client.list_users()
    if r.status_code != 200:
        show_api_error(r)
        return
    users = r.json()
    if not users:
        st.info("暂无用户。")
        return

    selected_uid = st.session_state.get("um_selected")
    confirm_del = st.session_state.get("um_confirm_delete")

    for u in users:
        uid = u["id"]
        is_active = u.get("is_active", True)
        with st.container():
            col_email, col_role, col_active, col_edit, col_del = st.columns([3, 2, 1, 1, 1])
            label_style = "" if is_active else "opacity:0.45;"
            col_email.markdown(f"<span style='{label_style}'>**{u['email']}**</span>", unsafe_allow_html=True)
            col_role.markdown(f"`{u['role']}`")
            active_label = "启用" if is_active else "禁用"
            if col_active.button(active_label, key=f"active-{uid}", use_container_width=True,
                                 help="切换账号启用/禁用状态"):
                r_act = client.update_active(uid, not is_active)
                if r_act.status_code == 200:
                    st.rerun()
                else:
                    show_api_error(r_act)
            if col_edit.button("编辑", key=f"edit-{uid}", use_container_width=True):
                st.session_state["um_selected"] = uid if selected_uid != uid else None
                st.session_state["um_confirm_delete"] = None
                st.rerun()
            with col_del:
                st.markdown("<div class='danger-btn'>", unsafe_allow_html=True)
                if st.button("删除", key=f"del-{uid}", use_container_width=True):
                    st.session_state["um_confirm_delete"] = uid
                    st.session_state["um_selected"] = None
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

            if selected_uid == uid:
                with st.form(f"edit-form-{uid}"):
                    ec1, ec2 = st.columns([2, 3])
                    new_role = ec1.selectbox("新角色", roles, index=roles.index(u["role"]), key=f"role-{uid}")
                    new_pwd = ec2.text_input("新密码（留空保持不变）", type="password", key=f"pwd-{uid}")
                    fa, fb, _ = st.columns([1, 1, 4])
                    if fa.form_submit_button("更新角色"):
                        r2 = client.update_role(uid, new_role)
                        if r2.status_code == 200:
                            st.success("角色已更新。")
                            st.session_state["um_selected"] = None
                            st.rerun()
                        else:
                            show_api_error(r2)
                    if fb.form_submit_button("重置密码"):
                        if not new_pwd:
                            st.warning("请输入新密码。")
                        else:
                            r3 = client.update_password(uid, new_pwd)
                            if r3.status_code == 200:
                                st.success("密码已重置。")
                                st.session_state["um_selected"] = None
                                st.rerun()
                            else:
                                show_api_error(r3)

            if confirm_del == uid:
                st.warning(f"确认删除 **{u['email']}**？此操作不可撤销。")
                yes_col, no_col, _ = st.columns([1, 1, 6])
                if yes_col.button("确认删除", key=f"del-yes-{uid}", type="primary"):
                    r_del = client.delete_user(uid)
                    if r_del.status_code == 204:
                        st.success("用户已删除。")
                        st.session_state["um_confirm_delete"] = None
                        st.rerun()
                    else:
                        show_api_error(r_del)
                if no_col.button("取消", key=f"del-no-{uid}"):
                    st.session_state["um_confirm_delete"] = None
                    st.rerun()

            st.markdown("<hr style='margin:0.3rem 0;border-color:#f1f5f9'>", unsafe_allow_html=True)
