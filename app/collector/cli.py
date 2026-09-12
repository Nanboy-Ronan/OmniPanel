"""CLI entrypoint: python -m app.collector <subcommand>

    bootstrap-login   Run locally (headed browser) — scan the portal's QR
                       code once, write a storage_state.json for later upload
                       to the server's 自动采集 admin page.
    collect            Run all enabled targets (or a filtered subset) and
                       upload results through the existing API. This is what
                       the rpa-collector.service unit executes.
    verify-session      Open a saved session against the portal and report
                       whether it's still logged in, without downloading.
                       Manual, single-target — used when provisioning/
                       debugging one account.
    verify-all          Check every enabled target's session (like
                       verify-session, but all of them at once) and send a
                       WeCom alert on anything dead/missing. This is what
                       the rpa-collector-verify.service unit executes, on an
                       earlier schedule than collect — see docs/collector.md.

`bootstrap-login` deliberately avoids importing anything under app.db /
app.collector.runner, so it can run from a bare checkout with just
`pip install -r requirements.txt && playwright install chromium` — no
database connection required.
"""
from __future__ import annotations

import argparse
import sys


def _cmd_bootstrap_login(args: argparse.Namespace) -> int:
    from .browser import fresh_context, looks_like_login, visible_text
    from .channels import CHANNELS_HOME_URL
    from .jd import JD_LOGIN_URL
    from .pugongying import PGY_HOME_URL, PGY_ROLE_SELECT_URL_MARKER
    from .xhs import XHS_LOGIN_URL, XHS_SELECT_ACCOUNT_URL_MARKER, XHS_WRONG_ACCOUNT_MARKER
    from .zhihu import ZHIHU_HOME_URL

    portal_url = {
        "xhs": XHS_LOGIN_URL, "zhihu": ZHIHU_HOME_URL, "pugongying": PGY_HOME_URL,
        "channels": CHANNELS_HOME_URL,
        "jd": JD_LOGIN_URL,
    }[args.platform]

    login_hint = {
        "xhs": "小红书专业号目前使用手机号+短信验证码",
        "zhihu": "优先选扫码登录——账号密码登录在此环境下曾触发知乎风控（10001 请求参数异常）",
        "channels": "视频号使用微信扫码",
        "jd": "请完成京麦商家账号登录",
    }.get(args.platform, "请按页面提示完成认证")
    print(f"打开 {args.platform} 登录页，请在弹出的浏览器窗口中完成登录（{login_hint}）……")
    with fresh_context(headless=False) as (page, context):
        page.goto(portal_url, wait_until="domcontentloaded")

        max_wait_ms = 8 * 60 * 1000  # generous first-run budget (find window, receive SMS, type code)
        poll_ms = 2000
        waited = 0
        consecutive_clean = 0
        printed_select_account_hint = False
        printed_role_select_hint = False
        # Require 2 consecutive "not a login page" reads before declaring success —
        # guards against a transient/partially-rendered page being mistaken for a
        # completed login (see docs/collector.md postmortem on the first XHS run).
        required_clean = 2
        while waited < max_wait_ms:
            page.wait_for_timeout(poll_ms)
            waited += poll_ms
            if waited % 30000 == 0:
                print(f"等待登录中……已过 {waited // 1000} 秒（最长 {max_wait_ms // 1000} 秒）")
            # XHS: one phone number can be linked to multiple professional
            # accounts. After SMS verification, login lands on an account-picker
            # page that is neither a login page nor a completed login — wait
            # through it rather than declaring success there (see xhs.py docstring).
            if args.platform == "xhs" and XHS_SELECT_ACCOUNT_URL_MARKER in page.url:
                consecutive_clean = 0
                if not printed_select_account_hint:
                    print("检测到「选择账号」页面，请点选要采集的那个小红书账号……")
                    printed_select_account_hint = True
                continue
            # PGY: same shape of trap as XHS's select-account picker above —
            # after SMS auth, login lands on a "选择身份" page (role-select)
            # that is neither a login page nor a completed login. The first
            # bootstrap-login ever run against this platform declared success
            # the moment it landed here and saved an incomplete session (see
            # pugongying.py docstring) — wait through it instead, same as XHS.
            if args.platform == "pugongying" and PGY_ROLE_SELECT_URL_MARKER in page.url:
                consecutive_clean = 0
                if not printed_role_select_hint:
                    print("检测到「选择身份」页面，请点击目标子账号旁边的「以子账号身份进入」……")
                    printed_role_select_hint = True
                continue
            if not looks_like_login(page.url, visible_text(page)):
                consecutive_clean += 1
                if consecutive_clean >= required_clean:
                    break
            else:
                consecutive_clean = 0
        else:
            from .browser import save_debug_artifacts
            save_debug_artifacts(page, f"{args.platform}_bootstrap_timeout")
            print(f"等待登录超时（{max_wait_ms // 1000} 秒），退出。当前页面："
                  f"{page.url}，已保存截图到调试目录以供排查。", file=sys.stderr)
            return 1

        # Same class of bug as the select-account picker itself (see xhs.py
        # module docstring): landing on /enterprise/home with a "not a login
        # page" read is NOT the same as landing on the *right* account — a
        # personal account passes every check above while showing this exact
        # text instead of real data. Only known for XHS today; catch it here
        # rather than letting it surface weeks later as an empty upload.
        if args.platform == "xhs" and XHS_WRONG_ACCOUNT_MARKER in visible_text(page):
            from .browser import save_debug_artifacts
            save_debug_artifacts(page, "xhs_bootstrap_wrong_account")
            print(f"登录成功，但当前账号看起来不对（页面显示「{XHS_WRONG_ACCOUNT_MARKER}」，"
                  f"很可能选中了个人号而不是目标企业号）。未保存登录态 —— 请重新执行 "
                  f"bootstrap-login，在「选择账号」页面点选正确的账号。", file=sys.stderr)
            return 1

        context.storage_state(path=args.out)
        print(f"登录态已保存到 {args.out}，请到管理页「自动采集」上传此文件。")
        return 0


def _cmd_verify_session(args: argparse.Namespace) -> int:
    from .channels import verify_channels_session
    from .errors import WrongAccountError
    from .paths import session_path
    from .pugongying import verify_pugongying_session
    from .jd import verify_jd_session
    from .xhs import verify_xhs_session
    from .zhihu import verify_zhihu_session

    path = session_path(args.platform, args.account_id)
    if not path.exists():
        print(f"未找到登录态文件: {path}", file=sys.stderr)
        return 1

    verify_fn = {
        "xhs": verify_xhs_session, "zhihu": verify_zhihu_session, "pugongying": verify_pugongying_session,
        "channels": verify_channels_session,
        "jd": verify_jd_session,
    }[args.platform]
    try:
        valid = verify_fn(path, headless=not args.headed)
    except WrongAccountError as exc:
        print(f"{path}: 登录态有效，但账号不对（很可能选错了子账号）。"
              f"请重新执行 bootstrap-login 并明确选择正确的账号。{exc}", file=sys.stderr)
        return 1

    if not valid:
        print(f"{path}: 登录态已过期")
        return 1
    print(f"{path}: 登录态有效")
    return 0


def _cmd_collect(args: argparse.Namespace) -> int:
    from .runner import run_collect

    return run_collect(
        triggered_by="manual" if (args.platform or args.account_id or args.content_type) else "schedule",
        only_platform=args.platform,
        only_account_id=args.account_id,
        only_content_type=args.content_type,
        dry_run=args.dry_run,
        headless=(False if args.headed else None),
    )


def _cmd_verify_all(args: argparse.Namespace) -> int:
    from .runner import run_verify

    return run_verify(headless=(False if args.headed else None))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.collector")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bootstrap = sub.add_parser("bootstrap-login", help="本地有头浏览器扫码登录，生成 storage_state.json")
    p_bootstrap.add_argument("--platform", required=True, choices=["xhs", "zhihu", "pugongying", "channels", "jd"])
    p_bootstrap.add_argument("--out", required=True, help="storage_state.json 输出路径")
    p_bootstrap.set_defaults(func=_cmd_bootstrap_login)

    p_verify = sub.add_parser("verify-session", help="检查已保存的登录态是否仍然有效")
    p_verify.add_argument("--platform", required=True, choices=["xhs", "zhihu", "pugongying", "channels", "jd"])
    p_verify.add_argument("--account-id", type=int, default=None)
    p_verify.add_argument("--headed", action="store_true")
    p_verify.set_defaults(func=_cmd_verify_session)

    p_collect = sub.add_parser("collect", help="执行一次采集（默认：全部已启用目标）")
    p_collect.add_argument("--platform", choices=["xhs", "zhihu", "pugongying", "channels", "jd"], default=None)
    p_collect.add_argument("--account-id", type=int, default=None)
    p_collect.add_argument("--content-type", choices=["article", "qa", "overview"], default=None)
    p_collect.add_argument("--dry-run", action="store_true", help="仅下载不上传")
    p_collect.add_argument("--headed", action="store_true", help="覆盖 COLLECTOR_HEADLESS=false 用于调试")
    p_collect.set_defaults(func=_cmd_collect)

    p_verify_all = sub.add_parser(
        "verify-all",
        help="巡检所有已启用目标的登录态是否有效，异常时告警（不下载、不上传）；建议比 collect 更早的时间点运行，留出补登录的时间",
    )
    p_verify_all.add_argument("--headed", action="store_true", help="覆盖 COLLECTOR_HEADLESS=false 用于调试")
    p_verify_all.set_defaults(func=_cmd_verify_all)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
