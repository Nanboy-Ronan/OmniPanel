"""Regenerate architecture.png and architecture.zh-CN.png.

The diagram is laid out as SVG and rendered through a headless browser so CJK
text is crisp. Run from the repository root with a Python environment that has
Playwright (the same one used by frontend/scripts/capture_screenshots.py):

    python docs/images/generate_architecture_diagram.py

OMNIPANEL_BROWSER_CHANNEL=chrome uses an installed Chrome instead of
Playwright's bundled Chromium.
"""
from __future__ import annotations

import os
from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).resolve().parent
W, H = 1680, 1060

# Fill, border, accent (title) per kind of box.
PALETTE = {
    "source": ("#FFF8EC", "#E9C98B", "#8A5A00"),
    "client": ("#EEF4FF", "#9DB8EC", "#1D4ED8"),
    "module": ("#FFFFFF", "#D5DCE6", "#0F172A"),
    "data": ("#F2F0FF", "#B9B0F0", "#4C3BC4"),
    "external": ("#F8FAFC", "#B4BDC9", "#334155"),
}

TEXT = {
    "en": {
        "out": "architecture.png",
        "title": "OmniPanel architecture",
        "subtitle": "Official exports and APIs in, one normalized PostgreSQL model out — no scraping.",
        "sources": "Data sources",
        "src": [
            ("Official API", ["WeChat Official Accounts", "daily scheduled sync"]),
            ("Collector agent", ["XHS · Pugongying · Zhihu", "WeChat Channels · JD merchant", "saved logins, run time budget"]),
            ("File upload", ["Youzan · Tmall · JD exports", ".xlsx / .csv, auto-detected", "progress, cancel, recovery"]),
        ],
        "browser": ("React console  /console/", "Signed in with WeCom QR · role-aware navigation"),
        "proxy": "Nginx · TLS · same-origin /api",
        "backend": "FastAPI backend  (app/)",
        "modules": [
            ("Ingestion (ETL)", ["detect → normalize → load", "raw platform rows kept", "order status & refund rule"]),
            ("Auth & access", ["WeCom SSO + JWT", "viewer / analyst / admin", "new members await approval"]),
            ("Analytics", ["commerce BI · content · Pgy", "cohorts · repurchase", "cross-platform identity"]),
            ("SQL console & NL-to-SQL", ["SELECT / WITH only", "row limit + statement timeout", "LLM writes SQL, server runs it"]),
            ("Weekly report", ["all-platform digest", "incomplete weeks flagged", "pushed to WeCom"]),
            ("Audit log", ["every sign-in attempt", "exports, views, changes", "source IP + device"]),
        ],
        "scheduler": ("Scheduler  (leader-elected)", "WeChat sync · collector watchdog · daily/monthly backups + restore drill · weekly report"),
        "db": "PostgreSQL",
        "public": ("public schema", ["orders + raw platform tables · media & campaign metrics", "users · saved views · operation_log", "app connects as rpa_app (read / write)"]),
        "reporting": ("reporting schema", ["masked views over public: no customer contact data,", "no secrets, no user table", "SQL console connects as rpa_sql_console (read-only login)"]),
        "backups": ("Backups", ["pg_dump daily / monthly · private 0600 files · validated"]),
        "views": "views",
        "external": [
            ("WeCom", ["OAuth sign-in", "alerts · report push"]),
            ("LLM providers", ["Anthropic · OpenAI", "DeepSeek · others"]),
            ("Redis", ["shared cache", "login rate limit"]),
            ("Off-site copy", ["hook after each", "backup"]),
        ],
        "optional": "optional / external",
        "legend": [("source", "data source"), ("client", "client"), ("module", "backend module"), ("data", "storage"), ("external", "optional / external")],
    },
    "zh": {
        "out": "architecture.zh-CN.png",
        "title": "OmniPanel 架构",
        "subtitle": "只接官方导出与官方 API，统一归一化到 PostgreSQL —— 不写爬虫。",
        "sources": "数据来源",
        "src": [
            ("官方 API", ["微信公众号", "每天定时同步"]),
            ("采集代理", ["小红书 · 蒲公英 · 知乎", "视频号 · 京东商家后台", "复用登录态，限时运行"]),
            ("文件上传", ["有赞 · 天猫 · 京东导出", ".xlsx / .csv 自动识别", "进度、取消、中断恢复"]),
        ],
        "browser": ("React 工作台  /console/", "企业微信扫码登录 · 按角色显示导航"),
        "proxy": "Nginx · TLS · 同源 /api",
        "backend": "FastAPI 后端  (app/)",
        "modules": [
            ("数据入库 (ETL)", ["识别平台 → 归一化 → 入库", "保留平台原始行", "订单状态与退款口径"]),
            ("鉴权与权限", ["企业微信单点登录 + JWT", "viewer / analyst / admin", "新成员需管理员开通"]),
            ("分析接口", ["商城 BI · 内容 · 蒲公英", "队列留存 · 复购", "跨平台客户识别"]),
            ("SQL 查询台与中文问数据", ["只允许 SELECT / WITH", "自动限行 + 语句超时", "大模型写 SQL，服务端执行"]),
            ("周报", ["全平台汇总", "订单未传完整会标注", "推送到企业微信"]),
            ("操作日志", ["每一次登录尝试", "导出、查看、数据变更", "来源 IP 与设备"]),
        ],
        "scheduler": ("后台调度  (leader 选举)", "公众号同步 · 采集看门狗 · 每日/月度备份与恢复演练 · 周报"),
        "db": "PostgreSQL",
        "public": ("public 模式", ["订单与平台原始表 · 内容与合作指标", "用户 · 保存的视图 · 操作日志", "应用以 rpa_app 连接（读写）"]),
        "reporting": ("reporting 模式", ["基于 public 的脱敏视图：不含客户联系方式、", "密钥和用户表", "查询台以 rpa_sql_console 连接（只读登录）"]),
        "backups": ("备份", ["pg_dump 每日 / 每月 · 文件权限 0600 · 自动校验"]),
        "views": "视图",
        "external": [
            ("企业微信", ["OAuth 登录", "告警 · 周报推送"]),
            ("大模型服务商", ["Anthropic · OpenAI", "DeepSeek 等"]),
            ("Redis", ["共享缓存", "登录限流"]),
            ("异地备份", ["每次备份后", "调用钩子"]),
        ],
        "optional": "可选 / 外部",
        "legend": [("source", "数据来源"), ("client", "客户端"), ("module", "后端模块"), ("data", "存储"), ("external", "可选 / 外部")],
    },
}

FONT = ("'PingFang SC','Hiragino Sans GB','Noto Sans CJK SC','Microsoft YaHei',"
        "-apple-system,'Segoe UI','Helvetica Neue',Arial,sans-serif")


class Svg:
    def __init__(self):
        self.parts: list[str] = []

    def add(self, s: str):
        self.parts.append(s)

    def text(self, x, y, s, size=13, weight=400, fill="#475569", anchor="start"):
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
                 f'text-anchor="{anchor}">{escape(s)}</text>')

    def box(self, x, y, w, h, kind, dashed=False, r=12):
        fill, stroke, _ = PALETTE[kind]
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="1.4"{dash}/>')

    def card(self, x, y, w, h, kind, title, lines, dashed=False, title_size=15, line_size=12.5):
        self.box(x, y, w, h, kind, dashed)
        accent = PALETTE[kind][2]
        self.add(f'<rect x="{x}" y="{y + 14}" width="3" height="18" rx="1.5" fill="{accent}"/>')
        self.text(x + 16, y + 28, title, size=title_size, weight=600, fill=accent)
        for i, line in enumerate(lines):
            self.text(x + 16, y + 52 + i * 19, line, size=line_size)

    def arrow(self, d, dashed=False, color="#64748B"):
        dash = ' stroke-dasharray="5 5"' if dashed else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dash} marker-end="url(#arrow)"/>')

    def line(self, d, dashed=False, color="#64748B"):
        dash = ' stroke-dasharray="5 5"' if dashed else ""
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6"{dash}/>')

    def pill(self, cx, cy, s, size=11.5):
        w = max(40, len(s) * size * (1.0 if any(ord(c) > 0x2E80 for c in s) else 0.58) + 18)
        self.add(f'<rect x="{cx - w / 2}" y="{cy - 11}" width="{w}" height="22" rx="11" '
                 f'fill="#FFFFFF" stroke="#CBD5E1"/>')
        self.text(cx, cy + 4, s, size=size, weight=500, fill="#334155", anchor="middle")


def build(t: dict) -> str:
    s = Svg()
    # Header
    s.text(48, 58, t["title"], size=26, weight=700, fill="#0F172A")
    s.text(48, 86, t["subtitle"], size=14, fill="#64748B")

    # Client row
    s.card(400, 120, 660, 46, "client", t["browser"][0], [], title_size=15)
    s.text(1044, 148, t["browser"][1], size=12.5, anchor="end", fill="#475569")
    s.arrow("M730 166 V206")
    s.box(560, 208, 340, 40, "client", r=20)
    s.text(730, 233, t["proxy"], size=12.5, weight=500, fill="#1D4ED8", anchor="middle")
    s.arrow("M730 248 V290")

    # Backend group
    s.add('<rect x="388" y="292" width="684" height="550" rx="18" fill="#F6F8FB" stroke="#CBD5E1" stroke-width="1.4"/>')
    s.text(410, 322, t["backend"], size=14, weight=700, fill="#0F172A")
    col = (410, 742)
    rows = (342, 478, 614)
    for i, (title, lines) in enumerate(t["modules"]):
        s.card(col[i % 2], rows[i // 2], 308, 118, "module", title, lines)
    s.card(410, 750, 640, 72, "module", t["scheduler"][0], [t["scheduler"][1]])

    # Sources column and bus into Ingestion
    s.text(48, 322, t["sources"], size=14, weight=700, fill="#0F172A")
    ys = ((342, 104), (462, 124), (602, 124))
    for (y, h), (title, lines) in zip(ys, t["src"]):
        s.card(48, y, 292, h, "source", title, lines)
        s.line(f"M340 {y + h / 2} H368")
    s.line(f"M368 {342 + 52} V{602 + 62}")
    s.arrow("M368 401 H408")

    # PostgreSQL group
    s.add('<rect x="1140" y="292" width="492" height="550" rx="18" fill="#FBFAFF" stroke="#CFC8F5" stroke-width="1.4"/>')
    s.text(1162, 322, t["db"], size=14, weight=700, fill="#0F172A")
    pub_t, pub_l = t["public"]
    s.card(1162, 342, 448, 118, "data", pub_t, pub_l)
    rep_t, rep_l = t["reporting"]
    s.card(1162, 556, 448, 118, "data", rep_t, rep_l)
    s.card(1162, 750, 448, 72, "data", t["backups"][0], t["backups"][1])
    s.arrow("M1386 460 V554", dashed=True, color="#7C6FD6")
    s.pill(1386, 507, t["views"])

    # Backend → database
    s.arrow("M1072 401 H1160")
    s.arrow("M1050 537 H1106 V615 H1160")
    s.arrow("M1050 786 H1160")

    # External services
    ext_y = 912
    xs = (388, 564, 740, 916)
    s.text(48, ext_y + 4, t["optional"], size=14, weight=700, fill="#0F172A")
    for x, (title, lines) in zip(xs, t["external"]):
        s.card(x, ext_y - 22, 156, 104, "external", title, lines, dashed=True, title_size=14, line_size=12)
        s.line(f"M{x + 78} 842 V{ext_y - 22}", dashed=True, color="#94A3B8")

    # Legend
    def label_w(label):
        return len(label) * (13 if any(ord(c) > 0x2E80 for c in label) else 7.2)
    lx = 1632 - sum(26 + label_w(l) + 22 for _, l in t["legend"]) + 22
    for kind, label in t["legend"]:
        fill, stroke, _ = PALETTE[kind]
        dash = ' stroke-dasharray="4 3"' if kind == "external" else ""
        s.add(f'<rect x="{lx}" y="51" width="18" height="14" rx="4" fill="{fill}" stroke="{stroke}"{dash}/>')
        s.text(lx + 26, 62, label, size=12.5)
        lx += 26 + label_w(label) + 22

    body = "\n".join(s.parts)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"
 font-family="{FONT}">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0 0 L10 5 L0 10 z" fill="#64748B"/></marker></defs>
<rect width="{W}" height="{H}" fill="#FFFFFF"/>
{body}
</svg>"""


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.environ.get("OMNIPANEL_BROWSER_CHANNEL") or None)
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        for t in TEXT.values():
            page.set_content(f"<html><body style='margin:0'>{build(t)}</body></html>")
            page.evaluate("document.fonts.ready")
            out = OUT_DIR / t["out"]
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
            print(f"Wrote {out.relative_to(OUT_DIR.parents[1])}")
        browser.close()


if __name__ == "__main__":
    main()
