from __future__ import annotations
import pandas as pd
import streamlit as st

from app.ui._helpers import _page_hero, show_api_error

_TABLES_SCHEMA: dict[str, list[tuple[str, str, str]]] = {
    "orders": [
        ("id", "integer", "主键"),
        ("order_date", "date", "下单日期"),
        ("order_id", "text", "原始平台订单号"),
        ("customer_key", "text", "客户标识（手机号 / 地址）"),
        ("platform", "text", "'youzan' | 'jd' | 'tmall'"),
        ("sku", "text", "商品 SKU 名称"),
        ("quantity", "integer", "购买数量"),
        ("price", "numeric", "订单金额（¥）"),
        ("receiver", "text", "收货人姓名"),
        ("receiver_phone", "text", "收货人手机号"),
        ("province", "text", "省份"),
        ("area", "text", "城市 / 地区"),
        ("full_address", "text", "完整收货地址"),
        ("buyer_nick", "text", "买家昵称"),
        ("coupon_name", "text", "使用的优惠券"),
        ("distributor", "text", "分销员 / 导购"),
    ],
    "customers": [
        ("customer_key", "text", "主键"),
        ("platform", "text", "来源平台"),
        ("first_order_date", "date", "首次下单日期"),
    ],
    "upload_batches": [
        ("id", "integer", "批次 ID"),
        ("filename", "text", "上传文件名"),
        ("platform", "text", "检测到的平台"),
        ("uploaded_at", "timestamp", "上传时间（UTC）"),
        ("row_count", "integer", "文件总行数"),
        ("inserted_orders", "integer", "新增订单数"),
        ("duplicate_rows", "integer", "重复跳过行数"),
        ("invalid_rows", "integer", "拒绝行数"),
        ("status", "text", "'completed' | 'error'"),
    ],
    "upload_rejected_rows": [
        ("id", "integer", "行 ID"),
        ("batch_id", "integer", "→ upload_batches.id"),
        ("platform", "text", "来源平台"),
        ("source_row_number", "integer", "文件中的原始行号"),
        ("reason", "text", "拒绝原因"),
    ],
    "operation_log": [
        ("id", "integer", "日志 ID"),
        ("user_id", "uuid", "操作用户"),
        ("action", "text", "'upload' | 'analysis' | 'sql_query' | 'create_user' | …"),
        ("timestamp", "timestamp", "时间戳（UTC）"),
        ("detail", "text", "JSON — 操作详情"),
    ],
}

_EXAMPLE_QUERIES = [
    (
        "按平台统计营业额",
        "SELECT platform,\n       COUNT(*) AS orders,\n       ROUND(SUM(price)::numeric, 2) AS revenue\nFROM orders\nGROUP BY platform\nORDER BY revenue DESC",
    ),
    (
        "订单量最高的 10 个 SKU",
        "SELECT sku,\n       COUNT(*) AS orders,\n       ROUND(SUM(price)::numeric, 2) AS revenue\nFROM orders\nGROUP BY sku\nORDER BY orders DESC\nLIMIT 10",
    ),
    (
        "各省独立客户数",
        "SELECT province,\n       COUNT(DISTINCT customer_key) AS customers\nFROM orders\nWHERE province IS NOT NULL\nGROUP BY province\nORDER BY customers DESC\nLIMIT 10",
    ),
    (
        "月度营业额趋势",
        "SELECT DATE_TRUNC('month', order_date) AS month,\n       COUNT(*) AS orders,\n       ROUND(SUM(price)::numeric, 2) AS revenue\nFROM orders\nGROUP BY month\nORDER BY month",
    ),
    (
        "复购客户（≥ 3 单）",
        "SELECT customer_key,\n       COUNT(*) AS orders,\n       ROUND(SUM(price)::numeric, 2) AS total_spend\nFROM orders\nGROUP BY customer_key\nHAVING COUNT(*) >= 3\nORDER BY orders DESC\nLIMIT 50",
    ),
    (
        "最近上传批次",
        "SELECT filename, platform, inserted_orders,\n       duplicate_rows, invalid_rows, uploaded_at\nFROM upload_batches\nORDER BY uploaded_at DESC\nLIMIT 20",
    ),
]


def _load_nl_providers(client) -> list[dict]:
    """Fetch (once per session) the AI providers configured server-side."""
    if "nl_providers" not in st.session_state:
        try:
            r = client.nl_sql_providers()
            st.session_state["nl_providers"] = (
                r.json().get("providers", []) if r.status_code == 200 else []
            )
        except Exception:
            st.session_state["nl_providers"] = []
    return st.session_state["nl_providers"]


def _render_nl_query(
    client, question: str, provider: str | None = None, model: str | None = None
) -> None:
    """Run a 中文问数据 request and render generated SQL + results (or error)."""
    if not (question or "").strip():
        st.warning("请输入问题。")
        return

    with st.spinner("正在理解问题并生成 SQL…"):
        r = client.nl_sql_query(question.strip(), provider, model)

    if r.status_code != 200:
        show_api_error(r, "问数据失败。")
        return

    payload = r.json()
    gen_sql = payload.get("sql") or ""
    explanation = payload.get("explanation") or ""
    err = payload.get("error")

    if explanation:
        st.caption(f"💡 {explanation}")
    if gen_sql:
        st.code(gen_sql, language="sql")

    if err:
        st.error(err)
        return

    rows = payload.get("rows", [])
    columns = payload.get("columns", [])
    row_count = payload.get("row_count", len(rows))
    if not rows:
        st.info("查询结果为空（0 行）。")
        return

    df = pd.DataFrame(rows, columns=columns)
    c1, c2 = st.columns([4, 1])
    c1.caption(f"共 {row_count:,} 行")
    c2.download_button(
        "下载 CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        "nl_query_result.csv",
        mime="text/csv",
        key="nl_dl",
    )
    st.dataframe(df, use_container_width=True)


def page_sql_console() -> None:
    client = st.session_state["client"]
    _page_hero("SQL 控制台")

    st.markdown(
        "<div class='sql-info-bar'>"
        "<span class='sql-rule'><span class='sql-dot'></span>仅支持 SELECT / WITH，不允许 DML 或 DDL</span>"
        "<span class='sql-rule'><span class='sql-dot'></span>最多返回 5,000 行 · 超时 10 秒</span>"
        "<span class='sql-rule'><span class='sql-dot'></span>每次查询均记录操作日志（含用户与行数）</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── 中文问数据 (AI) ─────────────────────────────────────────────────────────
    nl_ask = False
    nl_q = nl_provider = nl_model = None
    with st.container(border=True):
        st.markdown("##### 🔎 中文问数据")
        st.caption(
            "用中文描述你想查的数据，自动生成并执行 SQL。生成的语句会展示出来，"
            "可复制到下方的 SQL 框里修改后再跑。"
        )

        providers = _load_nl_providers(client)
        if not providers:
            st.info(
                "未配置 AI 服务商。请在服务端 .env 配置任一 API Key"
                "（如 `MINIMAX_API_KEY` 或 `ANTHROPIC_API_KEY`）后重启服务。"
            )
        else:
            by_label = {p["label"]: p for p in providers}
            pcol, mcol = st.columns(2)
            sel_label = pcol.selectbox(
                "服务商", list(by_label), key="nl_provider_label"
            )
            sel = by_label[sel_label]
            nl_provider = sel["id"]
            nl_model = mcol.selectbox(
                "模型", sel["models"], key=f"nl_model_{sel['id']}"
            )
            nl_q = st.text_input(
                "问题",
                placeholder="例如：最近 30 天每个平台的营业额和订单数",
                label_visibility="collapsed",
                key="nl_question",
            )
            nl_ask = st.button("生成并查询", key="nl_ask")

    if nl_ask:
        _render_nl_query(client, nl_q, nl_provider, nl_model)

    # ── 查询区域 ────────────────────────────────────────────────────────────────
    _default_sql = (
        "SELECT platform,\n"
        "       COUNT(*) AS orders,\n"
        "       ROUND(SUM(price)::numeric, 2) AS revenue\n"
        "FROM orders\n"
        "GROUP BY platform\n"
        "ORDER BY revenue DESC"
    )
    sql = st.text_area(
        "SQL 查询",
        value=_default_sql,
        height=180,
        label_visibility="collapsed",
        placeholder="SELECT …",
    )
    run_col, hint_col = st.columns([1, 5])
    run = run_col.button("执行查询", type="primary", use_container_width=True)
    hint_col.caption(
        "仅支持 SELECT / WITH · 不含分号 · 不允许 DML/DDL（含可写 CTE）· LIMIT ≤ 5,000 · 结果记录日志"
    )

    # ── Schema 参考 ─────────────────────────────────────────────────────────
    with st.expander("Schema 参考 — 可用表与字段", expanded=False):
        cols = st.columns(2)
        for idx, (table, fields) in enumerate(_TABLES_SCHEMA.items()):
            with cols[idx % 2]:
                st.markdown(f"**`{table}`**")
                lines = "\n".join(f"{col}  {typ}  -- {desc}" for col, typ, desc in fields)
                st.code(lines, language="sql")
        st.markdown("**原始平台表** *（每行对应源文件中的一行）*")
        st.caption(
            "· `youzan_orders` — 有赞导出文件 50+ 原始列\n"
            "· `jd_orders` — 京东导出文件 80+ 原始列\n"
            "· `tmall_orders` — 天猫导出文件 8 原始列\n\n"
            "三张表均含系统列：`id`、`batch_id`、`order_id`、`ingest_status`、`row_hash`、`created_at`。"
        )

    # ── 示例查询 ───────────────────────────────────────────────────────────────
    with st.expander("示例查询", expanded=False):
        for title, query in _EXAMPLE_QUERIES:
            st.markdown(f"**{title}**")
            st.code(query, language="sql")

    # ── Execute ───────────────────────────────────────────────────────────────────
    if not run:
        return

    if not sql.strip():
        st.warning("请输入 SQL 查询语句。")
        return

    with st.spinner("正在执行…"):
        r = client.sql_query(sql.strip())

    if r.status_code != 200:
        show_api_error(r, "查询失败。")
        return

    payload = r.json()
    rows = payload.get("rows", [])
    columns = payload.get("columns", [])
    row_count = payload.get("row_count", len(rows))

    if not rows:
        st.info("查询结果为空（0 行）。")
        return

    result_df = pd.DataFrame(rows, columns=columns)
    c1, c2 = st.columns([4, 1])
    c1.caption(f"共 {row_count:,} 行")
    c2.download_button(
        "下载 CSV",
        result_df.to_csv(index=False).encode("utf-8-sig"),
        "query_result.csv",
        mime="text/csv",
    )
    st.dataframe(result_df, use_container_width=True)
