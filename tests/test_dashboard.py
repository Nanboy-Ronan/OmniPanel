import os

os.environ.setdefault("API_URL", "http://127.0.0.1:9")
os.environ.setdefault("API_TIMEOUT", "1")

from streamlit.testing.v1 import AppTest


def test_login_view():
    at = AppTest.from_file("tests/run_dashboard.py")
    at.run(timeout=15)
    assert not at.exception
    # Password login form is intentionally hidden (WeCom is the required path).
    # No "登录" submit button should appear on the login page.
    assert "登录" not in [b.label for b in at.button]


def test_upload_view():
    at = AppTest.from_file("tests/run_dashboard.py")
    at.session_state["token"] = "dummy"
    at.session_state["page"] = "数据上传"
    at.run(timeout=15)
    # page_upload replaces st.header with a page-hero markdown; verify via the
    # "上传" form submit button that still exists inside _upload_card().
    assert any(b.label == "上传" for b in at.button)


def test_customers_page_exists():
    content = open("app/ui/pages/customers.py", encoding="utf-8").read()
    assert "客户管理" in content


def test_customers_page_uses_customer_key_display():
    content = open("app/ui/pages/customers.py", encoding="utf-8").read()
    # customer label must never fall back to raw customer_key (which is an address
    # for JD/Tmall); it should use buyer_nick or mobile (actual phone) instead.
    assert 'row.get("customer_key")' not in content.split("def _customer_label")[1].split("def ")[0]
    assert '"customer_key",' in content
    assert '"customer",' in content
    assert '"full_address",' in content
    assert 'st.column_config.TextColumn("客户"' in content
    assert 'df.groupby("province")["customer_key"].nunique()' in content


def test_overview_metrics(monkeypatch):
    """Dashboard should contain the Overview metric labels (Chinese)."""
    content = open("app/ui/pages/analysis.py", encoding="utf-8").read()
    assert "总订单数" in content
    assert "独立客户数" in content
    assert "复购率" in content


def test_signup_button_hidden(monkeypatch):
    """Sign-up switch should disappear when registration is closed."""
    monkeypatch.setattr(
        "app.ui.api_client.APIClient.registration_open",
        lambda self: False,
    )
    at = AppTest.from_file("tests/run_dashboard.py")
    at.run(timeout=15)
    labels = [b.label for b in at.button]
    assert all("注册" not in lbl for lbl in labels)


def test_user_creation_form_present():
    content = open("app/ui/pages/user_management.py", encoding="utf-8").read()
    assert "create-user" in content and "用户已创建" in content


def test_reset_password_option_present():
    content = open("app/ui/pages/user_management.py", encoding="utf-8").read()
    assert "重置密码" in content


def test_data_page_caching():
    content = open("app/ui/pages/data_browse.py", encoding="utf-8").read()
    assert "orders_df" in content and "刷新" in content


def test_data_page_can_inspect_source_platform_row():
    content = open("app/ui/pages/data_browse.py", encoding="utf-8").read()
    assert "on_select=\"rerun\"" in content
    assert "selection_mode=\"single-row\"" in content
    assert "client.order_raw" in content
    assert "原始平台记录" in content
    assert "原始行数" in content


def test_upload_requires_submit_button():
    content = open("app/ui/pages/upload.py", encoding="utf-8").read()
    assert "with st.form(f\"upload-{key}\"" in content
    assert "form_submit_button(\"上传\")" in content


def test_upload_summary_reflects_raw_ingest_counts():
    content = open("app/ui/pages/upload.py", encoding="utf-8").read()
    assert "_render_upload_summary" in content
    assert "来源行数" in content
    assert "新增订单" in content
    assert "原始行已存" in content
    assert "重复行" in content
    assert "拒绝行" in content
    assert "batch_id" in content


def test_database_status_visible_for_admins():
    content = open("app/ui/pages/db_status.py", encoding="utf-8").read()
    assert "db_status" in content
    assert "analysis_ready" in content


def test_jwt_not_stored_in_url():
    """Token must never be written to URL query params (security requirement)."""
    import glob
    ui_files = glob.glob("app/ui/**/*.py", recursive=True)
    combined = "\n".join(open(f, encoding="utf-8").read() for f in ui_files)
    assert "query_params.update(token=" not in combined
    assert 'query_params["token"]' not in combined

def test_media_analysis_tabs_exist():
    content = open("app/ui/pages/media.py", encoding="utf-8").read()
    assert "概览分析" in content
    assert "趋势分析" in content
    assert "互动分析" in content
    assert "文章明细" in content
    assert "分享率" in content
