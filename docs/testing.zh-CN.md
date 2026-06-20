# 测试指南

[English](testing.md) | [中文](testing.zh-CN.md)

## 运行测试套件

测试需要一个可访问的 PostgreSQL 服务器。它们不会动你真正的业务数据库——
`tests/conftest.py` 会在测试会话开始时创建一个名称唯一的 `rpa_test_*`
数据库，所有测试都跑在这个库上，结束后再删掉它。

```bash
export PG_TEST_URL=postgresql://user:pass@127.0.0.1:5432/postgres
make test          # 等价于：pytest tests/ -q
```

`PG_TEST_URL` 只需要指向一个你有权限的数据库服务器（它会先连到
`postgres` 这个管理数据库去执行 `CREATE DATABASE`/`DROP DATABASE`）——它本身指向的数据库不会被测试实际使用。如果没设置
`PG_TEST_URL`，会改用 `RAP_DATABASE_URL`，最后兜底用
`postgresql://rpa:rpa@127.0.0.1:5432/rpa`。

`conftest.py` 里内置的安全防护：
- `_db_url()` 会拒绝为任何不以 `rpa_test_` 开头（且不是管理库
  `postgres`）的数据库名构造连接串。
- 测试结束时的 `DROP DATABASE` 在删除前会再次校验 `rpa_test_` 前缀。
- 每个测试之间会自动清空所有数据表，但只有在确认当前数据库确实是
  `rpa_test_*` 之后才会执行。

也就是说，把 `PG_TEST_URL` 指向一个共享的/预发布环境的 Postgres 是安全的：最坏的情况也只是创建并删除了一个临时的
`rpa_test_<uuid>` 数据库。

## 测试套件覆盖了什么

- ETL：平台识别、归一化、重复上传时的去重逻辑
  （`test_full_row_uniqueness.py`、`test_multiplatform.py`、
  `test_platform_raw_tables.py`）
- API 接口：鉴权、上传、分析、SQL 查询台、管理后台
  （`test_api_endpoints.py`、`test_upload_validation.py`、
  `test_upload_background.py`、`test_sql_console.py`）
- 自媒体数据摄入：微信、小红书、知乎（`test_xhs.py`、
  `test_xhs_accounts.py`、`test_zhihu.py`）
- 缓存、限流、结构化日志、内容影响力分析

## 合成数据集

部分电商相关测试需要断言精确的聚合数值（订单数、营收总额、销量最高的
SKU）。这些测试没有依赖某个真实的订单导出文件，而是用了
`tests/sample_dataset.py` 里一份很小的、完全虚构的数据集：

```python
from sample_dataset import synthetic_youzan_df

df = synthetic_youzan_df()   # pandas DataFrame，列名是有赞原始表头
```

这份数据集的聚合特性是已知且有文档记录的——比如
`SAMPLE_CUSTOMER_PHONE`、`SAMPLE_CUSTOMER_JULY_ORDER_COUNT`、
`SAMPLE_CUSTOMER_JULY_TOTAL` 都和数据集一起导出，这样断言里就不需要散落各处的"魔法数字"。如果你要新增一个需要不同聚合形态的测试，优先扩展这个模块，而不是再内联一份临时的
CSV 字符串。

## 默认被跳过的测试

`tests/test_media_upload.py` 覆盖的是一条已经停用的微信 xlsx
上传链路（微信数据现在改为走官方 API 同步——见
[微信自动同步](wechat-auto-sync.zh-CN.md)）；整个模块被标记了
`pytest.mark.skip`。如果以后要重新启用这条上传链路，去掉文件顶部的
`pytestmark` 那一行即可。

这个仓库里故意没有任何测试依赖一份随仓库提交的真实客户导出文件——
`data/` 目录在 `.gitignore` 里；所有需要订单形态输入的测试都改用上面的合成数据集。

## 测试中的快速密码哈希

`conftest.py` 在测试会话里换上了一个简单的密码处理器
（`RAP_TEST_FAST_PASSWORDS=true`，测试会话默认设置），这样测试就不需要每次登录都承担真实 bcrypt
的计算成本。这个设置只在该环境变量被设置时生效——正常运行应用时完全不受影响。
