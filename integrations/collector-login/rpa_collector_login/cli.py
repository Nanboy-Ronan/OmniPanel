"""Runs in WorkBuddy's managed Python environment, on the employee computer."""
import argparse
import os
from urllib.parse import urlsplit
from .deployment import DEFAULT_ORIGIN
import html
import json
import sys
import time
import urllib.request
import urllib.error
import webbrowser

BASE = os.environ.get("OMNIPANEL_ORIGIN", DEFAULT_ORIGIN).rstrip("/")

def validate_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("A trusted HTTPS origin is required")
    if parsed.hostname in {"example.com", "dashboard.example.com"}:
        raise ValueError("Configure your deployment origin before pairing")
    return value

PORTALS = {
    "xhs": "https://pro.xiaohongshu.com/login",
    "pugongying": "https://pgy.xiaohongshu.com/",
    "channels": "https://channels.weixin.qq.com/platform",
}


def api(path, body=None, secret=None):
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + "/api/collector/repair" + path,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    # Never follow a redirect with collector credentials to another destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=120) as response:
        return json.load(response)


def restricted_state(context, platform):
    state = context.storage_state()
    suffix = "xiaohongshu.com" if platform in ("xhs", "pugongying") else "qq.com"
    def allowed(host):
        host = host.lstrip(".").lower()
        return host == suffix or host.endswith("." + suffix)
    from urllib.parse import urlsplit
    return {"cookies": [c for c in state["cookies"] if allowed(c["domain"])],
            "origins": [o for o in state["origins"] if allowed(urlsplit(o["origin"]).hostname or "")]}


def repair():
    validate_origin(BASE)
    pair = api("/pair", {})
    url = BASE + "/console/?repair=" + pair["id"]
    # Only public pairing id is printed; secret and cookies never leave memory
    # except in HTTPS requests to the fixed company origin.
    print("请在公司平台选择账号并确认本次修复：\n" + url, flush=True)
    webbrowser.open(url)
    deadline = time.monotonic() + 14 * 60
    path = "/local/" + pair["id"]
    while time.monotonic() < deadline:
        result = api(path, secret=pair["secret"])
        if result["status"] == "approved":
            break
        if result["status"] != "pending":
            raise RuntimeError("修复请求已结束，请重新发起。")
        time.sleep(3)
    else:
        raise RuntimeError("等待确认超时，请重新发起。")
    name, platform = result["name"], result["platform"]
    print(f"请登录 {name}。登录后切换到「完成登录」标签页，确认账号并提交。", flush=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai", viewport={"width":1440,"height":900})
            control = context.new_page()
            control.set_content('<html lang="zh"><title>完成登录</title><main style="max-width:640px;margin:70px auto;font:18px sans-serif">'
                '<h1>完成采集登录</h1><p>目标账号：<strong>' + html.escape(name) + '</strong></p>'
                '<p>请先在另一标签页完成扫码或短信登录，并选中此账号。然后返回这里提交。</p>'
                '<p>登录凭证将仅发送至 ' + html.escape(BASE) + ' 用于该账号采集，不会写入聊天或保存至本机文件。</p>'
                '<button style="padding:14px;font-size:18px" onclick="window.repairReady=true;this.disabled=true;this.textContent=\'正在复检，请稍候…\'">确认已登录目标账号，更新凭证</button></main></html>')
            portal = context.new_page()
            portal.goto(PORTALS[platform], wait_until="domcontentloaded", timeout=60000)
            while time.monotonic() < deadline:
                if control.evaluate("window.repairReady === true"):
                    break
                control.wait_for_timeout(500)
            else:
                raise RuntimeError("登录超时，请重新发起。")
            result = api(path, restricted_state(context, platform), secret=pair["secret"])
            messages = {"success": "登录态已更新并通过复检，后续自动采集会使用新登录态。",
                "collector_busy": "采集器正在运行，原登录态未替换，请稍后重新发起。",
                "verification_failed": "无法确认登录有效或账号名称一致，原登录态未替换。请确认账号后重新发起。"}
            print(json.dumps({"status": result["status"], "message": messages.get(result["status"], "请重新发起修复")}, ensure_ascii=False), flush=True)
            return 0 if result["status"] == "success" else 1
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="企业采集账号本地登录修复")
    parser.add_argument("command", choices=["repair", "status", "version"])
    args = parser.parse_args()
    try:
        if args.command == "version":
            print("1.0.0")
            return 0
        if args.command == "status":
            print(json.dumps({"ready": True, "credential_storage": "none", "authorization": "required_per_repair"}))
            return 0
        return repair()
    except urllib.error.HTTPError as exc:
        messages = {401: "请求无效，请重新发起。", 403: "你已不再是该账号负责人，请联系管理员。", 409: "请求已使用，请重新发起。", 410: "请求已过期，请重新发起。", 422: "登录凭证不正确，原登录态未替换。"}
        print(messages.get(exc.code, "平台暂时无法完成修复，请稍后重试。"), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):
        # Browser/network errors may contain sensitive portal URLs; don't log them.
        print("登录修复未完成。请保持浏览器打开并在 15 分钟内完成；如已关闭，请重新发起。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
