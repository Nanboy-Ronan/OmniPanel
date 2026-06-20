# 中文问数据 (Natural-Language → SQL)

[English](nl-to-sql.md) | [中文](nl-to-sql.zh-CN.md)

A feature in the SQL console that lets a user ask a question in plain
Chinese (e.g. "上个月有赞平台的营业额是多少") and get back generated SQL
plus its results, instead of writing SQL by hand.

## Why a dedicated feature instead of a generic text-to-SQL tool

A generic text-to-SQL layer only knows table/column names. It doesn't know
that on one platform `customer_key` is a phone number while on another it's
a shipping address (so the same person can look like two different
customers), that revenue is `SUM(price)`, that WeChat read counts are
cumulative rather than daily, or that `platform` only ever takes the values
`youzan` / `jd` / `tmall`. All of that business context is encoded once, in
the `SCHEMA_DOC` prompt constant in `app/utils/nl_to_sql.py`, and given to
the model on every request — that's what makes the generated SQL correct
rather than merely syntactically valid.

## How it works

```
question (Chinese)
   │
   ▼
generate_sql(question, provider, model)   ── app/utils/nl_to_sql.py
   │  builds a prompt = SCHEMA_DOC + business rules + the question
   │  calls the selected LLM provider
   ▼
(sql, explanation)                         ── untrusted candidate SQL
   │
   ▼
validate_sql_query → enforce_limit → SET LOCAL transaction_read_only = on
   │                                  (same pipeline as the manual SQL console)
   ▼
results + explanation returned to the UI; the query is logged to operation_log
```

The model is **never trusted** with execution. `generate_sql()` only
returns a string; the caller (`POST /analysis/nl-sql`) runs that string
through the exact same allow-list, auto-`LIMIT`, and read-only-transaction
guardrails as a human-typed console query (see
[Architecture → SQL console safety model](architecture.md#sql-console-safety-model)).
A bad or adversarial model response can fail to produce useful SQL; it
cannot produce a SQL statement that mutates data.

## Provider registry

Providers are declared once in `PROVIDERS` (`app/utils/nl_to_sql.py`) and
the UI's dropdown is populated from whichever ones have a key configured:

| Provider id | Label | Wire protocol | Models offered |
|---|---|---|---|
| `anthropic` | Anthropic (Claude) | Anthropic SDK | claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5 |
| `minimax` | MiniMax | OpenAI-compatible | MiniMax-M2, MiniMax-M1, MiniMax-Text-01, abab6.5s-chat |
| `deepseek` | DeepSeek | OpenAI-compatible | deepseek-chat, deepseek-reasoner |
| `moonshot` | Moonshot (Kimi) | OpenAI-compatible | moonshot-v1-8k/32k/128k |
| `zhipu` | 智谱 (GLM) | OpenAI-compatible | glm-4-plus, glm-4-flash |
| `openai` | OpenAI | OpenAI SDK | gpt-4o, gpt-4o-mini |

Every non-Anthropic provider speaks the OpenAI Chat Completions wire format,
so they all share one code path (`_complete_openai`) that's just pointed at
a different `base_url` + API key. Anthropic uses its own SDK/path
(`_complete_anthropic`) since its message format differs.

### How provider selection works at request time

- `available_providers()` returns only providers whose API key is set
  (`settings.<key_attr>` is non-empty), with the configured default
  (`NL_SQL_PROVIDER`) sorted first — this list populates the UI dropdown.
- If no provider has a key configured, the feature is unavailable
  (`NLToSQLNotConfigured`, surfaced to the UI as HTTP 503) and nothing else
  in the app is affected.
- A request carries only a provider id + model name (e.g. `"minimax"` +
  `"MiniMax-M2"`) — never a key. The server looks up the corresponding key
  from `settings` and calls that provider.
- If the requested model isn't valid for the chosen provider,
  `_resolve_model` falls back to `NL_SQL_MODEL` (if valid for that provider)
  and finally to the provider's first model.

### Configuring providers

Set whichever providers you want available, in `.env`:

```dotenv
NL_SQL_PROVIDER=minimax        # which provider is pre-selected by default
ANTHROPIC_API_KEY=
MINIMAX_API_KEY=sk-...
DEEPSEEK_API_KEY=
MOONSHOT_API_KEY=
ZHIPU_API_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=               # only used by the generic "openai" provider
```

You can configure as many as you like; users pick from a dropdown at query
time. Configuring zero keys simply disables the feature.

## Adding a new provider

If a new provider speaks the OpenAI Chat Completions format (most do), this
is a two-line change:

1. Add an API key field to `Settings` in `app/config.py`:
   ```python
   newprovider_api_key: str | None = None
   ```
2. Add an entry to `PROVIDERS` in `app/utils/nl_to_sql.py`:
   ```python
   "newprovider": ProviderSpec(
       "newprovider", "New Provider", "openai", "https://api.newprovider.com/v1",
       ("model-a", "model-b"),
       "newprovider_api_key",
   ),
   ```

No other code needs to change — `available_providers()`, `generate_sql()`,
and the UI dropdown all read from this registry.

## Extending the business-rules prompt

If you add tables or platform-specific quirks, extend `SCHEMA_DOC` in
`app/utils/nl_to_sql.py` rather than relying on the model to infer schema
from column names alone — that constant is the actual source of NL-to-SQL
accuracy, since it's the only place platform business semantics are spelled
out for the model.
