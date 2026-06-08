import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .common import (
    ARTIFACTS_DIR,
    LATEST_ACCOUNT_FILE,
    LATEST_MERGED_RESULT_FILE,
    LATEST_RESULT_FILE,
    LATEST_SUBSCRIPTION_RESULT_FILE,
    REGISTER_DIR,
    REGISTER_SCRIPT,
    cleanup_artifacts,
    read_json,
    write_json,
)
from .feishu_bitable import ensure_monitor_bitable, send_bitable_summary_to_collaborators
from .feishu_client import start_feishu_ws_client
from .online_studio_check import get_existing_account, register_online_account, run_studio_check
from .registered_subscription_check import run_subscription_check
from .run_online_checks import run_daily_checks, run_full_checks


def latest_account() -> dict[str, Any] | None:
    return read_json(LATEST_ACCOUNT_FILE, None)


def latest_result(result_type: str = "studio") -> dict[str, Any] | None:
    files = {
        "studio": LATEST_RESULT_FILE,
        "daily": LATEST_MERGED_RESULT_FILE,
        "subscription": LATEST_SUBSCRIPTION_RESULT_FILE,
    }
    return read_json(files.get(result_type, LATEST_RESULT_FILE), None)


def register_account() -> dict[str, Any] | None:
    account = register_online_account()
    if account:
        write_json(LATEST_ACCOUNT_FILE, account)
    return account


async def check_studio(options: dict[str, Any] | None = None) -> dict[str, Any]:
    return await run_studio_check({"mode": "registered-account", **(options or {})})


async def check_password_login(options: dict[str, Any] | None = None) -> dict[str, Any]:
    return await run_studio_check({"mode": "password-login", "label": "固定账号", **(options or {})})


async def check_daily() -> dict[str, Any]:
    return await run_daily_checks()


async def check_subscription() -> dict[str, Any]:
    return await run_subscription_check()


def send_screenshot(options: dict[str, Any] | None = None) -> dict[str, Any]:
    from .feishu_client import send_feishu_image

    options = options or {}
    result = latest_result(options.get("resultType", "studio")) or {}
    screenshot = options.get("screenshot") or result.get("screenshot") or result.get("finalScreenshot")
    if not screenshot:
        raise RuntimeError("没有可发送的截图。")
    send_feishu_image(screenshot)
    if str(screenshot).startswith(str(ARTIFACTS_DIR)):
        cleanup_artifacts([screenshot, LATEST_RESULT_FILE])
    return {"screenshot": screenshot}


def start_api_server(options: dict[str, Any] | None = None) -> None:
    args = [sys.executable, "-m", "src.api_server"]
    subprocess.run(args, cwd=Path(__file__).resolve().parent.parent, env={**os.environ, **((options or {}).get('env') or {})}, check=False)


async def run_online_check(options: dict[str, Any] | None = None) -> dict[str, Any] | None:
    options = options or {}
    mode = options.get("mode", "daily")
    if mode == "daily":
        return await check_daily()
    if mode == "full":
        return await run_full_checks()
    if mode == "subscription":
        return await check_subscription()
    if mode == "password-login":
        return await check_password_login(options)
    if mode == "existing-account":
        return await check_studio({**options, "useExistingAccount": True})
    if mode in ("registered-account", "studio"):
        return await check_studio(options)
    if mode == "register":
        return register_account()
    if mode == "bitable-init":
        return ensure_monitor_bitable()
    if mode == "bitable-summary":
        return send_bitable_summary_to_collaborators()
    raise RuntimeError(f"Unsupported online_check mode: {mode}")


async def run_cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="daily")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--use-existing-account", action="store_true")
    parser.add_argument("--screenshot")
    parser.add_argument("--no-feishu", action="store_true")
    args = parser.parse_args()
    command = args.command
    if command in ("api", "serve"):
        start_api_server()
        return
    if command == "feishu-ws":
        start_feishu_ws_client()
        return
    if command == "register":
        result = register_account()
    elif command == "bitable-init":
        result = ensure_monitor_bitable()
    elif command == "bitable-summary":
        result = send_bitable_summary_to_collaborators()
    elif command == "daily":
        result = await run_daily_checks(notify_feishu=not args.no_feishu)
    elif command == "full":
        result = await run_full_checks(notify_feishu=not args.no_feishu)
    elif command == "subscription":
        result = await run_subscription_check(notify_feishu=not args.no_feishu)
    elif command == "password-login":
        result = await check_password_login({"email": args.email, "password": args.password, "notifyFeishu": not args.no_feishu})
    elif command in ("studio", "check"):
        result = await check_studio({"useExistingAccount": args.use_existing_account, "notifyFeishu": not args.no_feishu})
    elif command == "send-screenshot":
        result = send_screenshot({"screenshot": args.screenshot})
    else:
        raise RuntimeError(f"未知 online_check 命令：{command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(run_cli())
