"""Application shutdown releases the independently configured SQL console pool."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize("body_fails", [False, True])
def test_shutdown_disposes_sql_console_pool(monkeypatch, body_fails):
    from app import main
    from app.db import sql_console

    console_engine = SimpleNamespace(dispose=AsyncMock())
    primary_engine = SimpleNamespace(dispose=AsyncMock())
    sync_engine = SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(sql_console, "_engine", console_engine)
    monkeypatch.setattr(sql_console, "_engine_url", "test-console-url")
    monkeypatch.setattr(main, "engine", primary_engine)
    monkeypatch.setattr(main, "sync_engine", sync_engine)
    monkeypatch.setattr(main, "try_become_leader", lambda: False)
    monkeypatch.setattr(main, "release_leader", Mock())
    app = SimpleNamespace(state=SimpleNamespace(workbuddy_mcp=None))

    async def run():
        async with main.lifespan(app):
            await asyncio.sleep(0)
            console_engine.dispose.assert_not_awaited()
            if body_fails:
                raise RuntimeError("lifespan body failed")

    if body_fails:
        with pytest.raises(RuntimeError, match="lifespan body failed"):
            asyncio.run(run())
    else:
        asyncio.run(run())

    console_engine.dispose.assert_awaited_once()
    assert sql_console._engine is None
    assert sql_console._engine_url is None
    primary_engine.dispose.assert_awaited_once()
    sync_engine.dispose.assert_called_once()


def test_dispose_unconfigured_sql_console_is_idempotent(monkeypatch):
    from app.db import sql_console

    monkeypatch.setattr(sql_console, "_engine", None)
    monkeypatch.setattr(sql_console, "_engine_url", None)
    asyncio.run(sql_console.dispose_engine())
    asyncio.run(sql_console.dispose_engine())
    assert sql_console._engine is None
    assert sql_console._engine_url is None
