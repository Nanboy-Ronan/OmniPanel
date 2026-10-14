"""Official CloudAgent SDK adapter. No hand-written ACP or guessed HTTP API."""
import asyncio
import logging
from collections.abc import Awaitable, Callable

from cloud_agent_sdk import CloudAgentClient, ManifestBuilder, PromptOptions, RuntimeCreateOptions, RuntimeConnectOptions, RequestOptions, APINotFoundError

from ...config import settings
from ...reports.workbuddy_snapshot import SYSTEM_PROMPT, prompt_for, validate_output

logger = logging.getLogger(__name__)


class WorkBuddyError(Exception):
    """Stable error codes only; SDK messages may contain service credentials."""


def configured() -> bool:
    return bool(settings.workbuddy_api_key and settings.workbuddy_api_key.get_secret_value().strip()
                and settings.workbuddy_agent_id and settings.workbuddy_agent_id.strip() and settings.workbuddy_source_app.strip())


def make_client():
    if not configured():
        raise WorkBuddyError("not_configured")
    return CloudAgentClient(api_key=settings.workbuddy_api_key.get_secret_value(),
                            source_app=settings.workbuddy_source_app, retry=False)


async def cleanup_runtime(runtime_id: str) -> bool:
    client = make_client()
    try:
        async with asyncio.timeout(20):
            runtime = await client.runtimes.get(runtime_id)
            await runtime.delete(RequestOptions(retry=False, timeout_ms=10000))
        return True
    except APINotFoundError:
        return True  # Already removed, including a crash after successful delete.
    except Exception as exc:
        # Error type is safe; neither SDK message, headers, nor link token are logged.
        logger.warning("WorkBuddy cleanup failed runtime=%s type=%s", runtime_id, type(exc).__name__)
        return False
    finally:
        await client.aclose()


async def generate(snapshot: dict, agent_id: str, manifest_version: str,
                   on_runtime: Callable[[str], Awaitable[None]],
                   on_cleanup: Callable[[bool], Awaitable[None]], *, client_factory=None):
    client = (client_factory or make_client)()
    runtime = session = None
    timeout = settings.workbuddy_report_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            manifest = (ManifestBuilder().id(agent_id).name("企业经营周报")
                        .version(manifest_version).system_prompt(SYSTEM_PROMPT).build())
            runtime = await client.runtimes.create(RuntimeCreateOptions(
                runtime_name=snapshot["snapshot_id"], agent_manifest=manifest,
                visibility="PRIVATE", retry=False, timeout_ms=min(timeout, 120) * 1000,
                metadata={"report_snapshot": snapshot["snapshot_id"]},
            ))
            await on_runtime(str(runtime.id))
            # SDK usually waits for RUNNING; handle the raw API's CREATING response too.
            while runtime.runtime_info.status == "CREATING":
                await asyncio.sleep(2)
                runtime = await client.runtimes.get(runtime.id)
            if runtime.runtime_info.status != "RUNNING":
                raise WorkBuddyError("runtime_not_running")
            session = runtime.sessions.default()
            await session.connect(RuntimeConnectOptions(reconnect_max_attempts=2))
            chunks, size, overflow = [], 0, False
            def on_chunk(notification):
                nonlocal size, overflow
                update = notification.update
                if str(notification.session_id) != str(session.id):
                    return
                if update.session_update == "agent_message_chunk" and update.content.type == "text":
                    size += len(update.content.text)
                    if size > 32000:
                        overflow = True
                    elif not overflow:
                        chunks.append(update.content.text)
            response = await session.prompt(prompt_for(snapshot), PromptOptions(
                on_chunk=on_chunk, timeout_ms=timeout * 1000, include_thinking=False,
            ))
            if response.stop_reason != "end_turn":
                raise WorkBuddyError("incomplete_turn")
            if overflow:
                raise WorkBuddyError("output_too_large")
            try:
                return validate_output("".join(chunks), snapshot)
            except (ValueError, TypeError):
                raise WorkBuddyError("invalid_output") from None
    except asyncio.CancelledError:
        raise
    except WorkBuddyError:
        raise
    except Exception as exc:
        logger.warning("WorkBuddy generation failed type=%s", type(exc).__name__)
        raise WorkBuddyError("provider_failed") from None
    finally:
        if session is not None:
            try:
                async with asyncio.timeout(10):
                    await session.disconnect()
            except Exception:
                pass
        if runtime is not None:
            deleted = False
            try:
                async with asyncio.timeout(15):
                    await runtime.delete(RequestOptions(retry=False, timeout_ms=10000))
                    deleted = True
            except Exception as exc:
                logger.warning("WorkBuddy cleanup failed type=%s", type(exc).__name__)
            try:
                await on_cleanup(deleted)
            except Exception:
                logger.warning("WorkBuddy cleanup state could not be saved")
        await client.aclose()
