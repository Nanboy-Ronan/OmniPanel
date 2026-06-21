"""Regenerates architecture.png / architecture.zh-CN.png from scratch.

Run with: python3 docs/images/generate_architecture_diagram.py
Requires matplotlib (already a project dependency via the analytics stack
isn't guaranteed, so install ad hoc if needed: pip install matplotlib).
"""
from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
from matplotlib.patches import FancyArrowPatch

LABELS = {
    "en": dict(
        streamlit=("Streamlit\ndashboard", ":8501"),
        fastapi=("FastAPI\nbackend", ":8000"),
        postgres=("PostgreSQL",),
        http="HTTP / REST\nJWT bearer token",
        orm="SQLAlchemy\n(async ORM)",
        internals_title="FastAPI backend (app/)",
        auth="Auth\nJWT + WeCom SSO\nRBAC",
        etl="ETL\ndetect → normalize\n→ load",
        analytics="Analytics\noverview, cohorts,\ncross-platform ID",
        sql="SQL console +\nNL-to-SQL",
        jobs="Background jobs\n(leader-elected)\nWeChat sync, backup",
        redis="Redis\n(optional)\nshared cache +\nrate limiter",
        llm="LLM providers\n(optional)\nAnthropic / OpenAI /\nMiniMax / DeepSeek /\nMoonshot / Zhipu",
        wecom="WeCom API\n(Tencent)\nOAuth profile",
        legend_required="required",
        legend_backend="backend module (app/)",
        legend_optional="optional / external",
        out="architecture.png",
    ),
    "zh": dict(
        streamlit=("Streamlit\n前端", ":8501"),
        fastapi=("FastAPI\n后端", ":8000"),
        postgres=("PostgreSQL",),
        http="HTTP / REST\nJWT bearer token",
        orm="SQLAlchemy\n(异步 ORM)",
        internals_title="FastAPI 后端 (app/)",
        auth="鉴权\nJWT + 企业微信单点登录\n角色权限控制",
        etl="ETL\n识别平台 → 归一化\n→ 入库",
        analytics="分析接口\n总览、队列留存、\n跨平台客户身份",
        sql="SQL 查询台 +\n中文问数据",
        jobs="后台任务\n(leader 选举)\n微信同步、备份",
        redis="Redis\n(可选)\n共享缓存 +\n登录限流",
        llm="大模型服务商\n(可选)\nAnthropic / OpenAI /\nMiniMax / DeepSeek /\n月之暗面 / 智谱",
        wecom="企业微信 API\n(腾讯)\nOAuth 用户信息",
        legend_required="必需组件",
        legend_backend="后端模块 (app/)",
        legend_optional="可选 / 外部组件",
        out="architecture.zh-CN.png",
    ),
}

REQUIRED_COLOR = dict(fc="#E8EEF9", ec="#3B5A8A")
BACKEND_COLOR = dict(fc="#EAF6EE", ec="#2F8F4E")
OPTIONAL_COLOR = dict(fc="#F5F5F5", ec="#888888", linestyle="dashed")


def box(ax, x, y, w, h, text, style, fontsize=10, **kw):
    p = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.6,
        facecolor=style["fc"],
        edgecolor=style["ec"],
        linestyle=style.get("linestyle", "solid"),
    )
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize, **kw)
    return (x, y, w, h)


def arrow(ax, p1, p2, label=None, style="-|>", color="#444444", lw=1.4, label_offset=(0, 0.12)):
    a = FancyArrowPatch(
        p1, p2, arrowstyle=style, mutation_scale=14,
        linewidth=lw, color=color, shrinkA=2, shrinkB=2,
    )
    ax.add_patch(a)
    if label:
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        ax.text(mx + label_offset[0], my + label_offset[1], label,
                ha="center", va="center", fontsize=8, color=color)


def generate(lang: str) -> None:
    L = LABELS[lang]
    if lang == "zh":
        plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang HK", "STHeiti", "DejaVu Sans"]
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(13, 8.2))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 8.2)
    ax.axis("off")

    # Top tier: Streamlit -> FastAPI -> PostgreSQL
    sx, sy, sw, sh = box(ax, 0.4, 6.6, 2.6, 1.3, "\n".join(L["streamlit"]), REQUIRED_COLOR, fontsize=11)
    fx, fy, fw, fh = box(ax, 5.2, 6.6, 2.6, 1.3, "\n".join(L["fastapi"]), BACKEND_COLOR, fontsize=11)
    px, py, pw, ph = box(ax, 10.0, 6.6, 2.6, 1.3, "\n".join(L["postgres"]), REQUIRED_COLOR, fontsize=11)

    arrow(ax, (sx + sw, sy + sh / 2), (fx, fy + fh / 2), label=L["http"], label_offset=(0, 0.35))
    arrow(ax, (fx, fy + fh / 2 - 0.25), (sx + sw, sy + sh / 2 - 0.25), color="#777777")
    arrow(ax, (fx + fw, fy + fh / 2), (px, py + ph / 2), label=L["orm"], label_offset=(0, 0.35))

    # Backend internals row
    ax.text(fx + fw / 2, 6.1, L["internals_title"], ha="center", va="top", fontsize=10, style="italic", color="#2F8F4E")

    internals = [L["auth"], L["etl"], L["analytics"], L["sql"], L["jobs"]]
    n = len(internals)
    iw, ih, gap = 2.2, 1.5, 0.25
    total_w = n * iw + (n - 1) * gap
    start_x = (13 - total_w) / 2
    iy = 3.9
    int_boxes = []
    for i, label in enumerate(internals):
        ix = start_x + i * (iw + gap)
        int_boxes.append(box(ax, ix, iy, iw, ih, label, BACKEND_COLOR, fontsize=8.5))
        bx, by, bw, bh = int_boxes[-1]
        arrow(ax, (bx + bw / 2, by + bh), (bx + bw / 2, fy), color="#2F8F4E", lw=1.0)

    # Optional / external row — each positioned under the internal module it
    # actually talks to, so the connector lines stay short and don't cross.
    rb = int_boxes[0]  # Auth
    eb = int_boxes[2]  # Analytics
    sb = int_boxes[3]  # SQL console

    oy = 1.0
    oh = 1.7

    wecom_w = 2.6
    wecom_x = rb[0] + rb[2] / 2 - wecom_w / 2
    wecom_box = box(ax, wecom_x, oy, wecom_w, oh, L["wecom"], OPTIONAL_COLOR, fontsize=8.5)

    redis_w = 2.6
    redis_x = eb[0] + eb[2] / 2 - redis_w / 2
    redis_box = box(ax, redis_x, oy, redis_w, oh, L["redis"], OPTIONAL_COLOR, fontsize=8.5)

    llm_w = 3.0
    llm_x = sb[0] + sb[2] / 2 - llm_w / 2 + 0.5
    llm_box = box(ax, llm_x, oy, llm_w, oh, L["llm"], OPTIONAL_COLOR, fontsize=8.5)

    arrow(ax, (wecom_box[0] + wecom_box[2] / 2, wecom_box[1] + wecom_box[3]),
          (rb[0] + rb[2] / 2, iy), color="#999999", style="-", lw=1.2)
    arrow(ax, (redis_box[0] + redis_box[2] / 2, redis_box[1] + redis_box[3]),
          (eb[0] + eb[2] / 2, iy), color="#999999", style="-", lw=1.2)
    arrow(ax, (llm_box[0] + llm_box[2] / 2, llm_box[1] + llm_box[3]),
          (sb[0] + sb[2] / 2, iy), color="#999999", style="-", lw=1.2)

    # Legend
    leg_y = 0.15
    box(ax, 0.4, leg_y, 0.3, 0.22, "", REQUIRED_COLOR, fontsize=1)
    ax.text(0.85, leg_y + 0.11, L["legend_required"], ha="left", va="center", fontsize=8.5)
    box(ax, 3.6, leg_y, 0.3, 0.22, "", BACKEND_COLOR, fontsize=1)
    ax.text(4.05, leg_y + 0.11, L["legend_backend"], ha="left", va="center", fontsize=8.5)
    box(ax, 7.0, leg_y, 0.3, 0.22, "", OPTIONAL_COLOR, fontsize=1)
    ax.text(7.45, leg_y + 0.11, L["legend_optional"], ha="left", va="center", fontsize=8.5)

    fig.tight_layout()
    fig.savefig(L["out"].__str__() if False else f"docs/images/{L['out']}", dpi=170, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    generate("en")
    generate("zh")
    print("wrote docs/images/architecture.png and docs/images/architecture.zh-CN.png")
