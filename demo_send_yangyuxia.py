import argparse
import asyncio
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

from src.common import ARTIFACTS_DIR
from src.feishu_bitable import bitable_url, load_bitable_config, send_member_text
from src.feishu_client import send_feishu_image_to_member
from src.run_online_checks import failure_screenshots, merged_report_text, run_full_checks


RECIPIENT_EMAIL = "yangyuxia@vastai3d.com"


def bitable_status_from_config() -> str:
    config = load_bitable_config()
    app_token = config.get("app_token")
    table_id = config.get("table_id")
    if app_token and table_id:
        return bitable_url(app_token, table_id)
    return "未配置 FEISHU_BITABLE_APP_TOKEN / FEISHU_BITABLE_TABLE_ID"


def send_smoke() -> dict:
    text = "\n".join(
        [
            "[调试] online_check_py 飞书发送冒烟",
            f"接收人：{RECIPIENT_EMAIL}",
            f"机器：{socket.gethostname()}",
            f"时间：{datetime.now(timezone.utc).isoformat()}",
            f"多维表格：{bitable_status_from_config()}",
        ]
    )
    return send_member_text({"member_type": "email", "member_id": RECIPIENT_EMAIL}, text)


def send_latest_full() -> dict:
    result_file = ARTIFACTS_DIR / "latest-full-result.json"
    if not result_file.exists():
        raise RuntimeError(f"没有找到最近一次 full 结果：{result_file}")
    merged = json.loads(result_file.read_text(encoding="utf-8"))
    bitable = merged.get("bitable") or {}
    bitable_status = bitable.get("url") or bitable_status_from_config()
    text = "[调试]\n" + merged_report_text(
        merged,
        "线上 Studio 注册/登录/订阅购买全量检查",
        bitable_status,
        "调试模式未发送",
    )
    response = {
        "text": send_member_text({"member_type": "email", "member_id": RECIPIENT_EMAIL}, text),
        "images": [],
    }
    for item in failure_screenshots(merged):
        response["images"].append(
            {
                "label": item["label"],
                "screenshot": item["screenshot"],
                **send_feishu_image_to_member("email", RECIPIENT_EMAIL, item["screenshot"]),
            }
        )
    return response


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-full", action="store_true", help="发送最近一次 full 合并报告和失败截图")
    parser.add_argument("--run-full", action="store_true", help="重新跑 full 全链路，并只发给杨玉霞")
    args = parser.parse_args()

    if args.run_full:
        result = await run_full_checks(notify_feishu=True, debug_recipient_email=RECIPIENT_EMAIL)
    elif args.latest_full:
        result = send_latest_full()
    else:
        result = send_smoke()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
