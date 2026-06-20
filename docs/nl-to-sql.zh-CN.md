# 中文问数据 (自然语言 → SQL)

[English](nl-to-sql.md) | [中文](nl-to-sql.zh-CN.md)

SQL 查询台里的一个功能，让用户可以直接用中文提问（比如"上个月有赞平台的营业额是多少"），自动拿到生成的 SQL 和查询结果，而不需要自己手写 SQL。

## 为什么不直接用通用的 text-to-SQL 工具

通用的 text-to-SQL 层只知道表名和列名。它不知道某个平台的
`customer_key` 是手机号、另一个平台是收货地址（同一个人在不同地址下单会被算成两个不同的客户），不知道营业额就是
`SUM(price)`，不知道微信的阅读数是累计值而不是按日的，也不知道
`platform` 只会取 `youzan` / `jd` / `tmall` 这几个值。这些业务背景全部被编码在
`app/utils/nl_to_sql.py` 的 `SCHEMA_DOC` prompt 常量里，并在每次请求时一起喂给模型——这才是生成出来的 SQL"对"而不只是"语法正确"的关键。

## 工作原理

```
用户问题（中文）
   │
   ▼
generate_sql(question, provider, model)   ── app/utils/nl_to_sql.py
   │  拼出 prompt = SCHEMA_DOC + 业务口径 + 用户问题
   │  调用选定的大模型服务商
   ▼
(sql, explanation)                         ── 尚不可信的候选 SQL
   │
   ▼
validate_sql_query → enforce_limit → SET LOCAL transaction_read_only = on
   │                                  （和手动 SQL 查询台完全一样的执行管线）
   ▼
结果 + 说明文字返回给前端；这次查询会被记录进 operation_log
```

模型生成的内容**永远不会被直接信任执行**。`generate_sql()`
只返回一段字符串；调用方（`POST /analysis/nl-sql`）会让这段字符串经过和人工在查询台里手敲查询完全相同的白名单检查、自动加
`LIMIT`、只读事务防护（见
[架构说明 → SQL 查询台安全模型](architecture.zh-CN.md#sql-查询台安全模型)）。一个表现糟糕或被诱导的模型响应，最多只是生不出有用的
SQL；它没办法生成一条会修改数据的 SQL 语句。

## 服务商注册表

服务商在 `app/utils/nl_to_sql.py` 的 `PROVIDERS` 里统一声明，前端下拉框里展示的是其中已经配置好 API Key 的那些：

| 服务商 id | 显示名称 | 通信协议 | 提供的模型 |
|---|---|---|---|
| `anthropic` | Anthropic (Claude) | Anthropic SDK | claude-opus-4-8、claude-sonnet-4-6、claude-haiku-4-5 |
| `minimax` | MiniMax | OpenAI 兼容协议 | MiniMax-M2、MiniMax-M1、MiniMax-Text-01、abab6.5s-chat |
| `deepseek` | DeepSeek | OpenAI 兼容协议 | deepseek-chat、deepseek-reasoner |
| `moonshot` | 月之暗面 (Kimi) | OpenAI 兼容协议 | moonshot-v1-8k/32k/128k |
| `zhipu` | 智谱 (GLM) | OpenAI 兼容协议 | glm-4-plus、glm-4-flash |
| `openai` | OpenAI | OpenAI SDK | gpt-4o、gpt-4o-mini |

除 Anthropic 之外的服务商全部走 OpenAI Chat Completions 协议，所以它们共用同一份调用逻辑（`_complete_openai`），区别只是
`base_url` 和 API Key 不同。Anthropic 用自己的 SDK 和单独的调用路径
（`_complete_anthropic`），因为它的消息格式不一样。

### 请求时如何选择服务商

- `available_providers()` 只会返回已经配置了 API Key 的服务商
  （`settings.<key_attr>` 非空），并把默认服务商
  （`NL_SQL_PROVIDER`）排在最前——前端下拉框就是用这份列表填充的。
- 如果一个服务商的 Key 都没配置，该功能直接不可用
  （`NLToSQLNotConfigured`，前端会看到 HTTP 503），但不影响应用的其他部分。
- 一次请求只会带上服务商 id + 模型名（比如 `"minimax"` +
  `"MiniMax-M2"`）——绝不会带 Key。服务端会根据这个 id 去 `settings`
  里查找对应的 Key，再调用对应服务商。
- 如果请求的模型对所选服务商无效，`_resolve_model` 会依次回退到
  `NL_SQL_MODEL`（如果对该服务商有效），最后回退到该服务商的第一个模型。

### 配置服务商

在 `.env` 里按需配置任意几个服务商：

```dotenv
NL_SQL_PROVIDER=minimax        # 默认预选哪个服务商
ANTHROPIC_API_KEY=
MINIMAX_API_KEY=sk-...
DEEPSEEK_API_KEY=
MOONSHOT_API_KEY=
ZHIPU_API_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=               # 只对通用的 "openai" 服务商生效
```

可以配置任意多个；用户在查询时通过下拉框选择。一个 Key 都不配，该功能就会被禁用。

## 新增一个服务商

如果新服务商走的是 OpenAI Chat Completions 协议（大多数都是），只需要改两处：

1. 在 `app/config.py` 的 `Settings` 里加一个 API Key 字段：
   ```python
   newprovider_api_key: str | None = None
   ```
2. 在 `app/utils/nl_to_sql.py` 的 `PROVIDERS` 里加一条：
   ```python
   "newprovider": ProviderSpec(
       "newprovider", "New Provider", "openai", "https://api.newprovider.com/v1",
       ("model-a", "model-b"),
       "newprovider_api_key",
   ),
   ```

不需要改其他任何代码——`available_providers()`、`generate_sql()`
以及前端下拉框都是从这份注册表读出来的。

## 扩展业务口径 prompt

如果你新增了表或者某个平台特有的"坑"，应该去扩展
`app/utils/nl_to_sql.py` 里的 `SCHEMA_DOC`，而不是指望模型仅凭列名就能猜对——这个常量才是中文问数据准确性的真正来源，因为它是唯一一处把平台业务语义明确讲给模型的地方。
