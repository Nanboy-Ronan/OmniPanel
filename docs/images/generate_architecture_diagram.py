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
W, H = 1680, 1230

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
        "subtitle": "One data foundation for dashboards, enterprise agents, and accountable reporting.",
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
            ("Weekly report queue", ["aggregate snapshot → agent", "evidence validation · retry", "saved reports · WeCom delivery"]),
            ("Audit log", ["sign-in · export · data changes", "agent queries & authorization", "source IP + device"]),
            ("Read-only MCP tools", ["commerce · media · definitions", "scoped tokens · revocation", "no customer identity or secrets"]),
            ("Collector login repair", ["owner approval per request", "isolated local browser", "verify account → replace session"]),
        ],
        "scheduler": ("Scheduler  (leader-elected)", "WeChat sync · collector watchdog · daily/monthly backups + restore drill · weekly report"),
        "db": "PostgreSQL",
        "public": ("public schema", ["orders + raw platform tables · media & campaign metrics", "users · OAuth grants · report jobs · audit log", "app connects as rpa_app (read / write)"]),
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
        "subtitle": "统一数据底座，连接分析工作台、企业智能体与可追溯的经营周报。",
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
            ("周报任务队列", ["汇总快照 → 智能体", "证据校验 · 失败重试", "保存报告 · 企业微信推送"]),
            ("操作日志", ["登录 · 导出 · 数据变更", "智能体查询与授权记录", "来源 IP 与设备"]),
            ("只读 MCP 工具", ["商城 · 媒体 · 统计口径", "按范围授权 · 可撤销令牌", "不开放客户身份与密钥"]),
            ("采集登录修复", ["每次请求由负责人确认", "独立的本地浏览器登录", "目标账号复检 → 替换登录态"]),
        ],
        "scheduler": ("后台调度  (leader 选举)", "公众号同步 · 采集看门狗 · 每日/月度备份与恢复演练 · 周报"),
        "db": "PostgreSQL",
        "public": ("public 模式", ["订单与平台原始表 · 内容与合作指标", "用户 · OAuth 授权 · 周报任务 · 操作日志", "应用以 rpa_app 连接（读写）"]),
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

    zh = t["out"].endswith("zh-CN.png")
    # Three entry points retain the original blue client cards.
    s.card(388, 120, 330, 104, "client", "React 工作台" if zh else "React workspace",
           ["企业微信扫码登录", "角色导航 · 看板 · SQL"] if zh else
           ["WeCom sign-in", "Role-aware dashboards · SQL"])
    s.card(742, 120, 330, 104, "client", "WorkBuddy 企业智能体" if zh else "WorkBuddy enterprise agents",
           ["MCP 查询 · OAuth / 个人令牌", "对话问数 · 发起登录修复"] if zh else
           ["MCP queries · OAuth / personal tokens", "Conversational analysis · login repair"], title_size=14)
    s.card(1140, 120, 492, 104, "external", "其他企业智能体" if zh else "Other enterprise agents",
           ["兼容 MCP + Bearer 令牌的客户端", "扩展接入路径，尚未逐平台验证"] if zh else
           ["Clients supporting MCP + Bearer tokens", "Extension path; per-platform validation pending"], dashed=True)
    s.line("M553 224 V250 H907 V224")
    s.arrow("M730 250 V270")
    s.arrow("M1386 224 V290 H902", dashed=True, color="#94A3B8")
    s.box(560, 272, 340, 40, "client", r=20)
    s.text(730, 297, t["proxy"], size=12.5, weight=500, fill="#1D4ED8", anchor="middle")
    s.arrow("M730 312 V358")

    # Backend modules: the original two-column grid, extended by one row.
    s.add('<rect x="388" y="360" width="684" height="686" rx="18" fill="#F6F8FB" stroke="#CBD5E1" stroke-width="1.4"/>')
    s.text(410, 390, t["backend"], size=14, weight=700, fill="#0F172A")
    for i, (title, lines) in enumerate(t["modules"]):
        s.card((410, 742)[i % 2], (410, 546, 682, 818)[i // 2], 308, 118, "module", title, lines)
    s.card(410, 954, 640, 72, "module", t["scheduler"][0], [t["scheduler"][1]])

    # Ingestion sources remain on the left.
    s.text(48, 390, t["sources"], size=14, weight=700, fill="#0F172A")
    for (y, h), (title, lines) in zip(((410,104),(530,124),(670,124)), t["src"]):
        s.card(48, y, 292, h, "source", title, lines)
        s.line(f"M340 {y + h / 2} H368")
    s.line("M368 462 V732")
    s.arrow("M368 469 H408")

    # Storage and the isolated SQL role remain on the right.
    s.add('<rect x="1140" y="360" width="492" height="686" rx="18" fill="#FBFAFF" stroke="#CFC8F5" stroke-width="1.4"/>')
    s.text(1162, 390, t["db"], size=14, weight=700, fill="#0F172A")
    s.card(1162, 410, 448, 118, "data", t["public"][0], t["public"][1])
    s.card(1162, 624, 448, 118, "data", t["reporting"][0], t["reporting"][1])
    s.card(1162, 954, 448, 72, "data", t["backups"][0], t["backups"][1])
    s.arrow("M1386 528 V622", dashed=True, color="#7C6FD6")
    s.pill(1386, 575, t["views"])
    s.arrow("M1072 469 H1160")
    s.arrow("M1050 605 H1106 V683 H1160")
    s.arrow("M1050 990 H1160")
    s.text(1386, 818, "业务表与受限报表视图分离" if zh else "Separate application data and reporting access",
           size=12.5, anchor="middle", fill="#7C6FD6")

    # WorkBuddy's report runtime is distinct from its MCP client.
    s.card(48, 1094, 292, 104, "external", "WorkBuddy Cloud Agent",
           ["可选周报服务 · 接收汇总快照", "返回分析结果，平台校验后保存"] if zh else
           ["Optional report runtime · aggregate input", "Analysis returned for server validation"], dashed=True, title_size=14, line_size=12)
    s.arrow("M410 741 H354 V1146 H342", dashed=True, color="#94A3B8")
    s.text(48, 1078, t["optional"], size=13, weight=700, fill="#0F172A")
    for x, (title, lines) in zip((388,564,740,916), t["external"]):
        s.card(x, 1094, 156, 104, "external", title, lines, dashed=True, title_size=14, line_size=12)
        s.line(f"M{x + 78} 1046 V1094", dashed=True, color="#94A3B8")
    s.text(1140, 1126, "部署说明" if zh else "Deployment notes", size=14, weight=600, fill="#334155")
    for i, line in enumerate((["实线：已实现的核心连接", "虚线：可选服务 / 待验证的客户端扩展", "外部 AI 按需启用；登录修复需用户确认"] if zh else
                            ["Solid: implemented core connections", "Dashed: optional services / client extensions", "External AI is opt-in; login repair needs approval"])):
        s.text(1140, 1152 + i * 20, line, size=12.5)

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
