"""Orchestrates one collector run across every enabled XHS account + Zhihu
content type, uploading through the existing API and recording a
CollectorRun per target. This is what `python -m app.collector collect`
(and, on the VM, the rpa-collector.service oneshot unit) executes.

Every dependency that talks to the network or the DB is a parameter with a
default, so tests can inject fakes without touching Postgres or Chromium.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from pathlib import Path
from typing import Callable, Literal

from ..config import settings as _default_settings
from ..utils.wecom_bot import send_wecom_alert
from .errors import DownloadTimeoutError, SessionExpiredError, UploadFailedError
from .paths import session_path
from .runs import finish_run, start_run

_logger = logging.getLogger(__name__)

# Module-level so tests can monkeypatch it (same pattern as start_run/finish_run/
# send_wecom_alert below) to short-circuit retry delays without real sleeping.
_sleep = time.sleep


_ZHIHU_CONTENT_LABEL = {"article": "文章", "qa": "问答"}


@dataclasses.dataclass
class Target:
    platform: Literal["xhs", "zhihu"]
    session_file: Path
    account_id: int | None = None
    content_type: str | None = None
    account_name: str | None = None

    @property
    def label(self) -> str:
        """Human-readable target name — this is what ends up in WeCom alerts,
        so it must identify the account the way the operator knows it, not by
        a database id they would have to look up.
        """
        if self.platform == "xhs":
            who = self.account_name or f"账号#{self.account_id}"
            return f"小红书·{who}"
        return f"知乎·{_ZHIHU_CONTENT_LABEL.get(self.content_type, self.content_type)}"


def build_targets(api, settings=None) -> list[Target]:
    """Enumerate every target that should be collected this run.

    XHS: one target per active xhs_accounts row, when collector_xhs_enabled.
    Zhihu: article + qa, when collector_zhihu_enabled.
    A target is still returned when its session file is missing — run_collect
    reports that as a failure (with an alert) rather than silently skipping it.
    """
    settings = settings or _default_settings
    targets: list[Target] = []

    if settings.collector_xhs_enabled:
        resp = api.xhs_accounts()
        resp.raise_for_status()
        for acc in resp.json():
            if not acc.get("is_active", True):
                continue
            targets.append(Target(
                platform="xhs",
                account_id=acc["id"],
                account_name=acc.get("name"),
                session_file=session_path("xhs", acc["id"]),
            ))

    if settings.collector_zhihu_enabled:
        zhihu_session = session_path("zhihu", None)
        for content_type in ("article", "qa"):
            targets.append(Target(
                platform="zhihu",
                content_type=content_type,
                session_file=zhihu_session,
            ))

    return targets


def _default_collect_fns() -> dict[str, Callable]:
    from .xhs import collect_xhs
    from .zhihu import collect_zhihu
    return {"xhs": collect_xhs, "zhihu": collect_zhihu}


def _default_verify_fns() -> dict[str, Callable]:
    from .xhs import verify_xhs_session
    from .zhihu import verify_zhihu_session
    return {"xhs": verify_xhs_session, "zhihu": verify_zhihu_session}


def _login_api_client(settings, *, log_prefix: str):
    """Create and log in the service-account API client used by both
    run_collect and run_verify. Returns (client, error_message); on failure
    client is None and error_message is the ready-to-send/log string."""
    from ..ui.api_client import APIClient
    api_client = APIClient(base_url=settings.collector_api_url)
    login_resp = api_client.login(settings.collector_service_email or "", settings.collector_service_password or "")
    if login_resp.status_code != 200:
        return None, f"{log_prefix} service-account 登录失败: {login_resp.status_code} {login_resp.text[:300]}"
    return api_client, None


def _collect_one(target: Target, collect_fns: dict[str, Callable], headless: bool | None) -> tuple[bytes, str]:
    if target.platform == "xhs":
        return collect_fns["xhs"](target.session_file, headless=headless)
    return collect_fns["zhihu"](target.session_file, target.content_type, headless=headless)


def _collect_with_retry(
    target: Target,
    collect_fns: dict[str, Callable],
    headless: bool | None,
    *,
    retries: int,
    delay_seconds: float,
) -> tuple[bytes, str]:
    """Retry _collect_one, but only for DownloadTimeoutError — a transient
    failure with a still-valid session. SessionExpiredError and anything else
    is never retried (retrying a dead session just wastes the whole delay)."""
    attempt = 1
    while True:
        try:
            return _collect_one(target, collect_fns, headless)
        except DownloadTimeoutError:
            if attempt >= retries:
                raise
            _logger.warning(
                "%s: 下载超时，%d/%d 次尝试失败，%ds 后重试",
                target.label, attempt, retries, delay_seconds,
            )
            _sleep(delay_seconds)
            attempt += 1


def _upload_one(api, target: Target, data: bytes, filename: str) -> dict:
    if target.platform == "xhs":
        resp = api.upload_xhs(data, filename, target.account_id)
    else:
        resp = api.upload_zhihu(data, filename, target.content_type)
    if resp.status_code != 200:
        raise UploadFailedError(f"upload rejected: {resp.status_code} {resp.text[:500]}")
    return resp.json()


def run_collect(
    *,
    settings=None,
    api_client=None,
    collect_fns: dict[str, Callable] | None = None,
    triggered_by: str = "schedule",
    only_platform: str | None = None,
    only_account_id: int | None = None,
    only_content_type: str | None = None,
    dry_run: bool = False,
    headless: bool | None = None,
) -> int:
    """Run the collector once. Returns a process exit code (0 = all targets
    succeeded, 1 = at least one target failed)."""
    settings = settings or _default_settings

    if not settings.collector_enabled:
        _logger.info("collector_disabled — exiting")
        return 0

    if api_client is None:
        api_client, err = _login_api_client(settings, log_prefix="[采集]")
        if err:
            _logger.error(err)
            send_wecom_alert(err)
            return 1

    collect_fns = collect_fns or _default_collect_fns()

    targets = build_targets(api_client, settings)
    if only_platform:
        targets = [t for t in targets if t.platform == only_platform]
    if only_account_id is not None:
        targets = [t for t in targets if t.account_id == only_account_id]
    if only_content_type is not None:
        targets = [t for t in targets if t.content_type == only_content_type]

    failures: list[str] = []
    successes: list[str] = []

    for target in targets:
        if not target.session_file.exists():
            msg = f"{target.label}: 未找到登录态文件 {target.session_file}，请本地重跑 bootstrap-login 并到管理页上传"
            _logger.warning(msg)
            failures.append(msg)
            continue

        run_id = start_run(
            target.platform,
            account_id=target.account_id,
            content_type=target.content_type,
            triggered_by=triggered_by,
        )
        try:
            data, filename = _collect_with_retry(
                target, collect_fns, headless,
                retries=settings.collector_collect_retries,
                delay_seconds=settings.collector_retry_delay_seconds,
            )

            if dry_run:
                finish_run(run_id, "success", rows_upserted=0, filename=filename)
                continue

            result = _upload_one(api_client, target, data, filename)
            rows = result.get("upserted", 0)
            finish_run(
                run_id, "success",
                rows_upserted=rows,
                filename=filename,
            )
            successes.append(f"{target.label}: {rows} 行")

        except SessionExpiredError as exc:
            finish_run(run_id, "session_expired", error_message=str(exc))
            msg = f"{target.label}: 登录态已过期，请本地重跑 bootstrap-login 并到管理页重传。{exc}"
            _logger.error(msg)
            failures.append(msg)

        except DownloadTimeoutError as exc:
            finish_run(run_id, "download_failed", error_message=str(exc))
            msg = f"{target.label}: 导出下载超时或失败。{exc}"
            _logger.error(msg)
            failures.append(msg)

        except UploadFailedError as exc:
            finish_run(run_id, "upload_failed", error_message=str(exc))
            msg = f"{target.label}: 上传失败。{exc}"
            _logger.error(msg, exc_info=exc)
            failures.append(msg)

        except Exception as exc:
            finish_run(run_id, "error", error_message=str(exc))
            msg = f"{target.label}: 未知错误。{exc}"
            _logger.error(msg, exc_info=exc)
            failures.append(msg)

    if failures:
        header = f"[采集告警] 本次运行 {len(failures)}/{len(targets)} 个目标失败：\n"
        lines = [f"- {f}" for f in failures]
        if successes:
            lines.append("")
            lines.append("成功的目标：")
            lines.extend(f"- {s}" for s in successes)
        send_wecom_alert(header + "\n".join(lines))
        return 1

    if targets and not dry_run and settings.wecom_notify_success:
        header = f"[采集] 本次运行全部成功（{len(successes)}/{len(targets)}）：\n"
        send_wecom_alert(header + "\n".join(f"- {s}" for s in successes))

    return 0


def run_verify(
    *,
    settings=None,
    api_client=None,
    verify_fns: dict[str, Callable] | None = None,
    headless: bool | None = None,
) -> int:
    """Proactively check every enabled target's saved session — no download,
    no upload — and alert on anything dead or missing.

    This is deliberately a separate entrypoint from run_collect, meant to run
    on its own, *earlier* schedule (see docs/collector.md): checking at
    collect time only reports a dead session after that day's data is
    already unrecoverable. Run early enough (e.g. the evening before) and a
    human still has time to redo bootstrap-login before the next collect
    window. Returns 0 if every target's session is valid, 1 otherwise.
    """
    settings = settings or _default_settings

    if not settings.collector_enabled:
        _logger.info("collector_disabled — skipping verify")
        return 0

    if api_client is None:
        api_client, err = _login_api_client(settings, log_prefix="[采集巡检]")
        if err:
            _logger.error(err)
            send_wecom_alert(err)
            return 1

    verify_fns = verify_fns or _default_verify_fns()
    targets = build_targets(api_client, settings)

    problems: list[str] = []

    for target in targets:
        if not target.session_file.exists():
            problems.append(f"{target.label}: 未找到登录态文件 {target.session_file}，请尽快 bootstrap-login 并上传")
            continue

        run_id = start_run(
            target.platform,
            account_id=target.account_id,
            content_type=target.content_type,
            triggered_by="verify",
        )
        try:
            valid = verify_fns[target.platform](target.session_file, headless=headless)
        except Exception as exc:
            finish_run(run_id, "error", error_message=str(exc))
            problems.append(f"{target.label}: 巡检本身出错，请人工检查。{exc}")
            continue

        if valid:
            finish_run(run_id, "success")
            continue

        finish_run(run_id, "session_expired")
        # Zhihu's selectors are still unverified placeholders (see zhihu.py
        # module docstring) — flag its verify result as best-effort so an
        # operator doesn't treat a false positive there as gospel the way
        # they should for XHS.
        note = "（知乎登录检测选择器尚未完全验证，结果仅供参考）" if target.platform == "zhihu" else ""
        problems.append(f"{target.label}: 登录态已过期{note}，请尽快本地重新执行 bootstrap-login 并到管理页重传")

    if problems:
        header = f"[采集巡检] 发现 {len(problems)} 个登录态异常，请尽快处理，避免影响下次采集：\n"
        send_wecom_alert(header + "\n".join(f"- {p}" for p in problems))
        return 1

    return 0
