import asyncio
import json
from pathlib import Path
from typing import Any

from .common import ARTIFACTS_DIR, write_json
from .feishu_bitable import append_monitor_record, send_bitable_summary_to_collaborators, send_member_text
from .feishu_client import send_feishu_image, send_feishu_image_to_member, send_feishu_text
from .online_studio_check import run_studio_check
from .registered_subscription_check import run_subscription_check


def now_iso() -> str:
    return __import__("datetime").datetime.utcnow().isoformat() + "Z"


def error_summary(error: Any) -> str:
    if not error:
        return ""
    return str(error).split("\n", 1)[0][:500]


def result_lines(result: dict[str, Any]) -> list[str]:
    status = "成功" if result.get("ok") else "失败"
    screenshot = result.get("screenshot") or result.get("finalScreenshot") or "-"
    lines = [
        f"{result.get('label') or result.get('mode')}：{status}",
        f"账号：{result.get('email') or '-'}",
        f"URL：{result.get('url') or '-'}",
        f"截图：{screenshot}",
    ]
    if result.get("couponCode") is not None:
        lines.extend([
            f"优惠券：{result.get('couponCode') or '-'}",
            f"购买前积分：{result.get('beforeCredits') if result.get('beforeCredits') is not None else '-'}",
            f"购买后积分：{result.get('afterCredits') if result.get('afterCredits') is not None else '-'}",
            f"积分是否增加：{'是' if result.get('creditIncreased') else '否'}",
        ])
    if result.get("error"):
        lines.append(f"错误：{error_summary(result.get('error'))}")
    return lines


def case_counts(merged: dict[str, Any]) -> dict[str, int]:
    results = merged.get("results", [])
    return {
        "total": len(results),
        "success": sum(1 for result in results if result.get("ok")),
        "failed": sum(1 for result in results if not result.get("ok")),
    }


def merged_report_text(merged: dict[str, Any], title: str, bitable_status: str, collaborator_status: str) -> str:
    counts = case_counts(merged)
    lines = [
        f"{title}：{'成功' if merged.get('ok') else '失败'}",
        f"开始：{merged.get('startedAt')}",
        f"结束：{merged.get('finishedAt')}",
        f"本次共跑 case：{counts['total']}，成功：{counts['success']}，失败：{counts['failed']}",
        f"多维表格：{bitable_status}",
        f"协作者汇总发送：{collaborator_status}",
        "",
    ]
    for index, result in enumerate(merged.get("results", []), 1):
        status = "成功" if result.get("ok") else "失败"
        lines.append(f"{index}. {result.get('label') or result.get('mode')}：{status} / {result.get('email') or '-'}")
        task_id = result.get("taskId")
        if not task_id:
            try:
                from .feishu_bitable import generation_task_id

                task_id = generation_task_id(result)
            except Exception:
                task_id = ""
        if task_id:
            lines.append(f"   TaskID：{task_id}")
        if result.get("url"):
            lines.append(f"   URL：{result.get('url')}")
        if result.get("couponCode") is not None:
            lines.extend([
                f"   优惠券：{result.get('couponCode') or '-'}",
                f"   购买前积分：{result.get('beforeCredits') if result.get('beforeCredits') is not None else '-'}",
                f"   购买后积分：{result.get('afterCredits') if result.get('afterCredits') is not None else '-'}",
                f"   积分是否增加：{'是' if result.get('creditIncreased') else '否'}",
            ])
        if result.get("error"):
            lines.append(f"   错误：{error_summary(result.get('error'))}")
    return "\n".join(lines)


def failure_screenshots(merged: dict[str, Any]) -> list[dict[str, str]]:
    screenshots = []
    for result in merged.get("results", []):
        if result.get("ok"):
            continue
        screenshot = result.get("finalScreenshot") or result.get("screenshot")
        if screenshot and Path(screenshot).exists():
            screenshots.append({"label": result.get("label") or result.get("mode") or "失败 case", "screenshot": screenshot})
    return screenshots


async def post_merged_feishu(merged: dict[str, Any], title: str, debug_recipient_email: str | None = None) -> None:
    bitable_status = "未写入"
    try:
        merged["bitable"] = append_monitor_record(merged, title)
        if merged["bitable"].get("skipped"):
            bitable_status = f"跳过：{merged['bitable'].get('reason')}"
        else:
            bitable_status = merged["bitable"].get("url") or "已写入"
    except Exception as error:
        merged["bitableError"] = str(error)
        bitable_status = f"写入失败：{error_summary(error)}"

    collaborator_status = "调试模式未发送"
    if not debug_recipient_email:
        try:
            merged["bitableSummary"] = send_bitable_summary_to_collaborators()
            collaborator_status = f"已发送给 {(merged.get('bitableSummary') or {}).get('memberCount')} 人"
        except Exception as error:
            merged["bitableSummaryError"] = str(error)
            collaborator_status = f"失败：{error_summary(error)}"

    text = merged_report_text(merged, title, bitable_status, collaborator_status)
    if debug_recipient_email:
        member = {"member_type": "email", "member_id": debug_recipient_email}
        merged["feishu"] = send_member_text(member, "[调试]\n" + text)
    else:
        merged["feishu"] = send_feishu_text(text)

    merged["feishuImages"] = []
    image_targets = failure_screenshots(merged)
    for item in image_targets:
        if debug_recipient_email:
            image_result = send_feishu_image_to_member("email", debug_recipient_email, item["screenshot"])
        else:
            image_result = send_feishu_image(item["screenshot"])
        merged["feishuImages"].append({**item, **image_result})


async def run_safely(options: dict[str, Any]) -> dict[str, Any]:
    try:
        return await run_studio_check(
            {
                **options,
                "notifyFeishu": False,
                "cleanupArtifacts": False,
                "resultFile": str(ARTIFACTS_DIR / f"{options['mode']}-latest-result.json"),
            }
        )
    except Exception as error:
        return {
            "ok": False,
            "label": options.get("label"),
            "mode": options.get("mode"),
            "startedAt": now_iso(),
            "finishedAt": now_iso(),
            "error": str(error),
        }


async def run_subscription_safely() -> dict[str, Any]:
    try:
        result = await run_subscription_check(notify_feishu=False)
        result["label"] = "新注册账号订阅购买"
        result["mode"] = "subscription"
        return result
    except Exception as error:
        return {
            "ok": False,
            "label": "新注册账号订阅购买",
            "mode": "subscription",
            "startedAt": now_iso(),
            "finishedAt": now_iso(),
            "error": str(error),
        }


async def run_daily_checks(notify_feishu: bool = True, debug_recipient_email: str | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "ok": False,
        "startedAt": now_iso(),
        "results": [],
    }
    merged["results"].append(
        await run_safely(
            {
                "label": "固定账号",
                "mode": "password-login",
                "email": __import__("os").getenv("ACCOUNT_EMAIL"),
                "password": __import__("os").getenv("ACCOUNT_PASSWORD"),
            }
        )
    )
    merged["results"].append(await run_safely({"label": "新注册账号", "mode": "registered-account"}))
    merged["finishedAt"] = now_iso()
    merged["ok"] = all(result.get("ok") for result in merged["results"])
    if notify_feishu:
        try:
            await post_merged_feishu(merged, "线上 Studio 文生模型每日检查", debug_recipient_email)
        except Exception as error:
            merged["feishuError"] = str(error)
    write_json(ARTIFACTS_DIR / "latest-merged-result.json", merged)
    return merged


async def run_full_checks(notify_feishu: bool = True, debug_recipient_email: str | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "ok": False,
        "startedAt": now_iso(),
        "results": [],
    }
    merged["results"].append(
        await run_safely(
            {
                "label": "老用户登录",
                "mode": "existing-account",
                "useExistingAccount": True,
            }
        )
    )
    merged["results"].append(await run_safely({"label": "新注册账号", "mode": "registered-account"}))
    merged["results"].append(await run_subscription_safely())
    merged["finishedAt"] = now_iso()
    merged["ok"] = all(result.get("ok") for result in merged["results"])
    if notify_feishu:
        try:
            await post_merged_feishu(merged, "线上 Studio 注册/登录/订阅购买全量检查", debug_recipient_email)
        except Exception as error:
            merged["feishuError"] = str(error)
    write_json(ARTIFACTS_DIR / "latest-full-result.json", merged)
    return merged


async def main() -> None:
    merged = await run_daily_checks()
    print(json.dumps(merged, ensure_ascii=False, indent=2))
    if not merged.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
