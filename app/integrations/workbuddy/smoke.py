"""python -m app.integrations.workbuddy.smoke: synthetic Agent call, no DB or push."""
import asyncio
import uuid

from .reports import WorkBuddyError, configured, generate
from ...reports.workbuddy_snapshot import PROMPT_VERSION
from ...config import settings


async def main():
    if not configured():
        raise SystemExit("请先配置 WORKBUDDY_API_KEY、WORKBUDDY_AGENT_ID、WORKBUDDY_SOURCE_APP。")
    snapshot = {
        "snapshot_id": f"smoke-{uuid.uuid4().hex[:12]}", "prompt_version": PROMPT_VERSION,
        "period": {"start": "2026-09-07", "end": "2026-09-13", "timezone": "Asia/Shanghai"},
        "metrics": [{"metric_id": "synthetic.orders", "value": 12,
                     "definition": "合成测试订单数，不是真实经营数据。", "complete": True}],
        "warnings": ["仅用于验证接口的合成数据。"],
    }

    async def on_runtime(runtime_id):
        print(f"Runtime 已创建：{runtime_id}", flush=True)

    async def on_cleanup(deleted):
        print("Runtime 已删除。" if deleted else "Runtime 清理未确认，请在控制台核对上面的编号。", flush=True)

    print(f"合成测试：{snapshot['snapshot_id']}（仅创建临时 Runtime，不发送通知）", flush=True)
    try:
        output = await generate(snapshot, settings.workbuddy_agent_id, "1.0", on_runtime, on_cleanup)
    except WorkBuddyError as exc:
        raise SystemExit(f"联调未通过：{exc}；若创建超时，请按上述合成测试名在控制台核对 Runtime。") from None
    print("联调通过：流式响应完成，JSON 结构和指标引用校验通过。")
    print(output.narrative())


if __name__ == "__main__":
    asyncio.run(main())
