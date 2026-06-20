"""Natural-language → SQL generation for the 中文问数据 (ask-your-data) feature.

Translates a Chinese question into a single read-only PostgreSQL ``SELECT`` using
Claude, given a fixed schema plus the platform-specific business semantics that a
generic BI tool does not know (platform enum values, what ``customer_key`` means
per platform, price units, the cumulative WeChat-metric caveat, …).

The generated SQL is **not** trusted. Callers must still run it through
``validate_sql_query`` / ``enforce_limit`` and the read-only execution path,
exactly like the manual SQL console — this module only produces a candidate
query, it never executes anything.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..config import settings


class NLToSQLNotConfigured(RuntimeError):
    """Raised when the feature is unavailable (no API key / SDK not installed)."""


class NLToSQLError(RuntimeError):
    """Raised when the model call fails or returns an unusable result."""


# ── Provider registry ─────────────────────────────────────────────────────────
# Each provider declares which SDK it speaks, its endpoint (for OpenAI-compatible
# ones), the models the UI offers, and which settings field holds its API key.
# Add a provider by adding an entry here + a key field in config.Settings.
@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    sdk: str               # "anthropic" | "openai"
    base_url: str | None   # for openai-compatible providers; None → SDK default
    models: tuple[str, ...]
    key_attr: str          # name of the settings attribute holding the API key


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        "anthropic", "Anthropic (Claude)", "anthropic", None,
        ("claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"),
        "anthropic_api_key",
    ),
    "minimax": ProviderSpec(
        "minimax", "MiniMax", "openai", "https://api.minimaxi.com/v1",
        ("MiniMax-M2", "MiniMax-M1", "MiniMax-Text-01", "abab6.5s-chat"),
        "minimax_api_key",
    ),
    "deepseek": ProviderSpec(
        "deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1",
        ("deepseek-chat", "deepseek-reasoner"),
        "deepseek_api_key",
    ),
    "moonshot": ProviderSpec(
        "moonshot", "Moonshot (Kimi)", "openai", "https://api.moonshot.cn/v1",
        ("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        "moonshot_api_key",
    ),
    "zhipu": ProviderSpec(
        "zhipu", "智谱 (GLM)", "openai", "https://open.bigmodel.cn/api/paas/v4",
        ("glm-4-plus", "glm-4-flash"),
        "zhipu_api_key",
    ),
    "openai": ProviderSpec(
        "openai", "OpenAI", "openai", None,
        ("gpt-4o", "gpt-4o-mini"),
        "openai_api_key",
    ),
}


def _provider_key(spec: ProviderSpec) -> str | None:
    return getattr(settings, spec.key_attr, None)


def available_providers() -> list[dict]:
    """Providers that have an API key configured, in registry order with the
    default provider first. Returned to the UI to populate the dropdowns."""
    out = [
        {"id": spec.id, "label": spec.label, "models": list(spec.models)}
        for spec in PROVIDERS.values()
        if _provider_key(spec)
    ]
    default = (settings.nl_sql_provider or "").strip().lower()
    out.sort(key=lambda p: p["id"] != default)  # default provider to the front
    return out


def default_provider_id() -> str | None:
    """The provider the UI should pre-select: the configured default if it has a
    key, else the first configured provider, else None."""
    configured = available_providers()
    if not configured:
        return None
    return configured[0]["id"]


def _resolve_model(spec: ProviderSpec, requested: str | None) -> str:
    """Pick the model to use: an explicit valid request wins, then the env
    default if compatible with this provider, else the provider's first model."""
    if requested and requested in spec.models:
        return requested
    if settings.nl_sql_model and settings.nl_sql_model in spec.models:
        return settings.nl_sql_model
    return spec.models[0]


# Business-semantics + schema description fed to the model. This is the moat:
#口径 a generic text-to-SQL tool cannot know without this context.
SCHEMA_DOC = """\
数据库为 PostgreSQL。所有业务分析都基于 orders 表。

表 orders（已归一化的订单，所有分析的主表）:
  id            integer   主键
  order_date    date      下单 / 付款日期
  order_id      text      原始平台订单号
  customer_key  text      客户标识。有赞=收货人手机号；京东/天猫=收货地址（注意：同一人不同地址会算作不同客户）
  platform      text      取值只有 'youzan' | 'jd' | 'tmall'
  sku           text      商品 SKU 名称
  quantity      integer   购买数量
  price         numeric   订单金额，单位人民币（¥）
  receiver          text  收货人姓名
  receiver_phone    text  收货人手机号
  province          text  省份
  area              text  城市 / 地区
  full_address      text  完整收货地址
  buyer_nick        text  买家昵称
  coupon_name       text  使用的优惠券
  distributor       text  分销员 / 导购

表 customers（客户身份索引，不含金额统计）:
  customer_key      text  主键
  platform          text  来源平台
  first_order_date  date  首次下单日期

表 upload_batches（每次上传一行）:
  id, filename, platform, uploaded_at(timestamp), row_count,
  inserted_orders, duplicate_rows, invalid_rows, status

表 operation_log（操作审计日志）:
  id, user_id(uuid), action(text), timestamp, detail(text, JSON)

表 xhs_posts（小红书笔记指标，按 (title, publish_date) 去重）:
  id, title, publish_date(date), genre, impressions(曝光), views(观看量),
  likes(点赞), comments(评论), collects(收藏), new_followers(涨粉), shares(分享)

原始平台表 youzan_orders / jd_orders / tmall_orders 保留源文件原始列，一般无需查询。

口径要点:
- 营业额 = SUM(price)；客单价 = AVG(price)；独立客户数 = COUNT(DISTINCT customer_key)。
- 平台过滤用 platform IN ('youzan','jd','tmall')，不要凭空构造其它平台值。
- 涉及金额时用 ROUND(SUM(price)::numeric, 2) 保留两位。
- "最近 N 天" 用 order_date >= CURRENT_DATE - INTERVAL 'N days'。
- 复购客户 = 同一 customer_key 订单数 >= 2。
"""

SYSTEM_PROMPT = f"""你是一个把中文业务问题转换为 PostgreSQL 查询的助手。

{SCHEMA_DOC}

规则:
1. 只能生成单条只读查询，必须以 SELECT 或 WITH 开头。
2. 严禁任何写操作或 DDL（INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE 等），即使在 CTE 内也不行。
3. 不要使用分号；不要使用 SELECT ... INTO。
4. 如果结果可能很多，加上 LIMIT（不超过 5000）。
5. 使用上面给出的真实表名和列名，不要臆造字段。
6. 如果问题无法用现有表回答，把 sql 设为空字符串，并在 explanation 里用中文说明原因。

只返回一个 JSON 对象，不要包含 markdown 代码块或任何额外文字，格式为:
{{"sql": "<生成的SQL，单行或多行均可>", "explanation": "<一句中文说明这条查询在做什么>"}}"""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_model_json(text: str) -> dict:
    """Extract the JSON object from the model's reply, tolerating code fences."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the first {...} block if the model added stray prose.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise NLToSQLError("模型返回的内容无法解析为 SQL。")


async def _complete_anthropic(question: str, model: str, api_key: str) -> str:
    """Call Claude via the Anthropic SDK and return the raw text reply."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:  # pragma: no cover - import guard
        raise NLToSQLNotConfigured("中文问数据未启用：anthropic SDK 未安装。") from exc

    client = AsyncAnthropic(api_key=api_key)
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
    except Exception as exc:  # noqa: BLE001 - surface any SDK/network error uniformly
        raise NLToSQLError(f"模型调用失败：{exc}") from exc

    return "".join(
        getattr(block, "text", "") for block in resp.content
        if getattr(block, "type", None) == "text"
    )


async def _complete_openai(
    question: str, model: str, api_key: str, base_url: str | None
) -> str:
    """Call an OpenAI-compatible API (OpenAI / MiniMax / Moonshot / DeepSeek …).

    The provider is selected purely by ``base_url`` + ``model``; the wire format
    is the OpenAI Chat Completions schema, which all of these share.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - import guard
        raise NLToSQLNotConfigured("中文问数据未启用：openai SDK 未安装。") from exc

    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=1500,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - surface any SDK/network error uniformly
        raise NLToSQLError(f"模型调用失败：{exc}") from exc

    return resp.choices[0].message.content or ""


async def generate_sql(
    question: str,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Translate a Chinese question into ``(sql, explanation)``.

    ``provider`` / ``model`` come from the UI selection; when omitted they fall
    back to ``NL_SQL_PROVIDER`` / ``NL_SQL_MODEL``. The provider is looked up in
    :data:`PROVIDERS` and its API key is read from settings server-side — the
    client only ever sends the provider id and model name.

    Raises:
        NLToSQLNotConfigured: provider unknown or its API key not configured.
        NLToSQLError: the model call failed or returned an unusable result.
    """
    pid = (provider or settings.nl_sql_provider or "anthropic").strip().lower()
    spec = PROVIDERS.get(pid)
    if spec is None:
        raise NLToSQLNotConfigured(
            f"不支持的服务商：{pid}（支持 {', '.join(PROVIDERS)}）。"
        )

    api_key = _provider_key(spec)
    if not api_key:
        raise NLToSQLNotConfigured(
            f"中文问数据未启用：请在服务端配置 {spec.label} 的 API Key。"
        )

    use_model = _resolve_model(spec, model)

    if spec.sdk == "anthropic":
        text_out = await _complete_anthropic(question, use_model, api_key)
    else:
        # Named providers carry their own base_url; the generic "openai" entry
        # may be repointed at any OpenAI-compatible endpoint via OPENAI_BASE_URL.
        base_url = spec.base_url or settings.openai_base_url
        text_out = await _complete_openai(question, use_model, api_key, base_url)

    data = _parse_model_json(text_out)
    sql = str(data.get("sql") or "").strip()
    explanation = str(data.get("explanation") or "").strip()
    return sql, explanation
