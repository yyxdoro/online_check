import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .common import ARTIFACTS_DIR, read_json, write_json
from .feishu_client import BASE_URL, CHAT_ID, disabled, feishu_headers, send_feishu_text

CONFIG_FILE = ARTIFACTS_DIR / "feishu-bitable-config.json"
DEFAULT_BITABLE_NAME = "线上 Studio 监控日报"
DEFAULT_TABLE_NAME = "每日汇总"
TEXT_FIELD_TYPE = 1
ATTACHMENT_FIELD_TYPE = 17

TEXT_FIELD_NAMES = [
    "检查日期",
    "检查标题",
    "整体结果",
    "开始时间",
    "结束时间",
    "老用户结果",
    "老用户账号",
    "老用户生成URL",
    "老用户TaskID",
    "老用户截图路径",
    "新注册结果",
    "新注册账号",
    "新注册生成URL",
    "新注册TaskID",
    "新注册截图路径",
    "订阅结果",
    "订阅账号",
    "订阅生成URL",
    "订阅TaskID",
    "优惠券",
    "购买前积分",
    "购买后积分",
    "积分是否增加",
    "订阅截图路径",
    "失败原因",
    "结果JSON路径",
    "原始结果摘要",
]

ATTACHMENT_FIELD_NAMES = [
    "老用户截图附件",
    "新注册截图附件",
    "订阅截图附件",
]


def is_bitable_configured() -> bool:
    config = load_bitable_config()
    return bool(config.get("app_token") and config.get("table_id"))


def load_bitable_config() -> dict[str, Any]:
    config = read_json(CONFIG_FILE, {}) or {}
    app_token = os.getenv("FEISHU_BITABLE_APP_TOKEN") or config.get("app_token")
    table_id = os.getenv("FEISHU_BITABLE_TABLE_ID") or config.get("table_id")
    return {
        **config,
        "app_token": app_token,
        "table_id": table_id,
        "app_name": os.getenv("FEISHU_BITABLE_NAME") or config.get("app_name") or DEFAULT_BITABLE_NAME,
        "table_name": os.getenv("FEISHU_BITABLE_TABLE_NAME") or config.get("table_name") or DEFAULT_TABLE_NAME,
    }


def save_bitable_config(config: dict[str, Any]) -> None:
    write_json(CONFIG_FILE, config)


def feishu_request(method: str, path: str, **kwargs) -> dict[str, Any]:
    headers = feishu_headers()
    if not headers:
        raise RuntimeError("缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET，无法调用飞书多维表格。")
    response = requests.request(
        method,
        f"{BASE_URL}{path}",
        headers={**headers, **kwargs.pop("headers", {})},
        timeout=kwargs.pop("timeout", 60),
        **kwargs,
    )
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    if response.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"飞书多维表格接口失败: {method} {path} {response.status_code} {data}")
    return data


def create_bitable_app(name: str) -> dict[str, Any]:
    data = feishu_request(
        "POST",
        "/bitable/v1/apps",
        headers={"Content-Type": "application/json; charset=utf-8"},
        json={"name": name},
    )
    app = data.get("data", {}).get("app") or data.get("data", {})
    app_token = app.get("app_token") or app.get("token")
    if not app_token:
        raise RuntimeError(f"创建飞书多维表格后没有返回 app_token: {data}")
    return {"app_token": app_token, "raw": data}


def list_tables(app_token: str) -> list[dict[str, Any]]:
    data = feishu_request("GET", f"/bitable/v1/apps/{app_token}/tables")
    return data.get("data", {}).get("items") or data.get("data", {}).get("tables") or []


def create_table(app_token: str, table_name: str) -> dict[str, Any]:
    data = feishu_request(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        headers={"Content-Type": "application/json; charset=utf-8"},
        json={"table": {"name": table_name}},
    )
    table = data.get("data", {}).get("table") or data.get("data", {})
    table_id = table.get("table_id")
    if not table_id:
        raise RuntimeError(f"创建飞书数据表后没有返回 table_id: {data}")
    return {"table_id": table_id, "raw": data}


def ensure_monitor_bitable() -> dict[str, Any]:
    config = load_bitable_config()
    app_token = config.get("app_token")
    table_id = config.get("table_id")
    app_name = config["app_name"]
    table_name = config["table_name"]

    created = False
    if not app_token:
        app = create_bitable_app(app_name)
        app_token = app["app_token"]
        created = True

    if not table_id:
        tables = list_tables(app_token)
        matched = next((item for item in tables if item.get("name") == table_name), None)
        if matched:
            table_id = matched.get("table_id")
        elif tables and not created:
            table_id = tables[0].get("table_id")
            table_name = tables[0].get("name") or table_name
        else:
            table = create_table(app_token, table_name)
            table_id = table["table_id"]

    if not table_id:
        raise RuntimeError("未能获取飞书多维表格 table_id。")

    ensured = {
        "app_token": app_token,
        "table_id": table_id,
        "app_name": app_name,
        "table_name": table_name,
    }
    save_bitable_config(ensured)
    ensure_monitor_fields(app_token, table_id)
    return ensured


def list_fields(app_token: str, table_id: str) -> list[dict[str, Any]]:
    data = feishu_request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields")
    return data.get("data", {}).get("items") or data.get("data", {}).get("fields") or []


def create_field(app_token: str, table_id: str, field_name: str, field_type: int) -> None:
    feishu_request(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        headers={"Content-Type": "application/json; charset=utf-8"},
        json={"field_name": field_name, "type": field_type},
    )


def ensure_monitor_fields(app_token: str, table_id: str) -> None:
    existing = {item.get("field_name") or item.get("name") for item in list_fields(app_token, table_id)}
    for field_name in TEXT_FIELD_NAMES:
        if field_name not in existing:
            create_field(app_token, table_id, field_name, TEXT_FIELD_TYPE)
    for field_name in ATTACHMENT_FIELD_NAMES:
        if field_name not in existing:
            create_field(app_token, table_id, field_name, ATTACHMENT_FIELD_TYPE)


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def date_timestamp_ms(value: Any) -> int | str:
    parsed = parse_iso_datetime(value)
    if parsed:
        day = parsed.date()
    else:
        try:
            day = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value or "")[:10]
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def datetime_timestamp_ms(value: Any) -> int | str:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return str(value or "")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def field_types(app_token: str, table_id: str) -> dict[str, int]:
    return {str(item.get("field_name") or item.get("name") or ""): int(item.get("type") or 0) for item in list_fields(app_token, table_id)}


def adapt_field_values(fields: dict[str, Any], types: dict[str, int]) -> dict[str, Any]:
    adapted = dict(fields)
    for name in ["检查日期", "开始时间", "结束时间"]:
        if types.get(name) == 5:
            converter = date_timestamp_ms if name == "检查日期" else datetime_timestamp_ms
            adapted[name] = converter(adapted.get(name))
    return adapted


def status_text(result: dict[str, Any] | None) -> str:
    if not result:
        return "-"
    return "成功" if result.get("ok") else "失败"


def first_line(value: Any) -> str:
    lines = str(value or "").splitlines()
    return (lines[0] if lines else "")[:500]


def result_screenshot(result: dict[str, Any] | None) -> str:
    if not result:
        return ""
    for key in ["finalScreenshot", "screenshot", "stripeScreenshot", "pricingScreenshot", "beforeScreenshot"]:
        value = result.get(key)
        if value:
            return str(value)
    return ""


def upload_bitable_attachment(app_token: str, file: str | Path) -> list[dict[str, str]]:
    headers = feishu_headers()
    if not headers:
        raise RuntimeError("缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET，无法上传多维表格附件。")
    path = Path(file)
    if not path.exists():
        return []
    with path.open("rb") as image:
        response = requests.post(
            f"{BASE_URL}/drive/v1/medias/upload_all",
            headers=headers,
            data={
                "file_name": path.name,
                "parent_type": "bitable_file",
                "parent_node": app_token,
                "size": str(path.stat().st_size),
            },
            files={"file": (path.name, image, "image/png")},
            timeout=120,
        )
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    if response.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"上传飞书多维表格附件失败: {response.status_code} {data}")
    file_token = data.get("data", {}).get("file_token") or data.get("file_token")
    if not file_token:
        raise RuntimeError(f"上传飞书多维表格附件后没有返回 file_token: {data}")
    return [{"file_token": file_token}]


def generation_evidence(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {}
    evidence = result.get("generationEvidence") or {}
    if not evidence and isinstance(result.get("studioResult"), dict):
        evidence = result["studioResult"].get("generationEvidence") or {}
    return evidence


def generation_task_id(result: dict[str, Any] | None) -> str:
    evidence = generation_evidence(result)
    responses = evidence.get("successfulResponses") or evidence.get("responses") or []
    for item in responses:
        if "operation/text_to_model" not in str(item.get("url") or ""):
            continue
        data = item.get("responseJson") or {}
        payload = data.get("data") if isinstance(data, dict) else None
        if isinstance(payload, dict) and payload.get("task_id"):
            return str(payload["task_id"])
    return ""


def generation_url(result: dict[str, Any] | None) -> str:
    evidence = generation_evidence(result)
    return str((evidence or {}).get("pageUrlAfterSubmit") or (result or {}).get("url") or "")


def find_result(merged: dict[str, Any], *, mode: str | None = None, label: str | None = None) -> dict[str, Any] | None:
    for result in merged.get("results", []):
        if mode and result.get("mode") == mode:
            return result
        if label and result.get("label") == label:
            return result
    return None


def compact_summary(merged: dict[str, Any]) -> str:
    items = []
    for result in merged.get("results", []):
        items.append(
            {
                "label": result.get("label"),
                "mode": result.get("mode"),
                "ok": result.get("ok"),
                "email": result.get("email"),
                "url": result.get("url"),
                "error": first_line(result.get("error")),
                "taskId": generation_task_id(result),
                "screenshot": result_screenshot(result),
                "beforeCredits": result.get("beforeCredits"),
                "afterCredits": result.get("afterCredits"),
                "creditIncreased": result.get("creditIncreased"),
            }
        )
    return json.dumps(items, ensure_ascii=False)[:9000]


def failure_summary(merged: dict[str, Any]) -> str:
    failures = []
    for result in merged.get("results", []):
        if result.get("ok"):
            continue
        failures.append(f"{result.get('label') or result.get('mode')}: {first_line(result.get('error')) or '失败'}")
    return "\n".join(failures)


def monitor_record_fields(merged: dict[str, Any], title: str, app_token: str) -> dict[str, Any]:
    old_user = find_result(merged, mode="existing-account") or find_result(merged, label="老用户登录")
    registered = find_result(merged, mode="registered-account") or find_result(merged, label="新注册账号")
    subscription = find_result(merged, mode="subscription")
    result_file = ARTIFACTS_DIR / ("latest-full-result.json" if subscription else "latest-merged-result.json")

    return {
        "检查日期": (merged.get("finishedAt") or merged.get("startedAt") or "")[:10],
        "检查标题": title,
        "整体结果": "成功" if merged.get("ok") else "失败",
        "开始时间": str(merged.get("startedAt") or ""),
        "结束时间": str(merged.get("finishedAt") or ""),
        "老用户结果": status_text(old_user),
        "老用户账号": str((old_user or {}).get("email") or ""),
        "老用户生成URL": generation_url(old_user),
        "老用户TaskID": generation_task_id(old_user),
        "老用户截图路径": result_screenshot(old_user),
        "老用户截图附件": upload_bitable_attachment(app_token, result_screenshot(old_user)) if result_screenshot(old_user) else [],
        "新注册结果": status_text(registered),
        "新注册账号": str((registered or {}).get("email") or ""),
        "新注册生成URL": generation_url(registered),
        "新注册TaskID": generation_task_id(registered),
        "新注册截图路径": result_screenshot(registered),
        "新注册截图附件": upload_bitable_attachment(app_token, result_screenshot(registered)) if result_screenshot(registered) else [],
        "订阅结果": status_text(subscription),
        "订阅账号": str((subscription or {}).get("email") or ""),
        "订阅生成URL": generation_url(subscription),
        "订阅TaskID": generation_task_id(subscription),
        "优惠券": str((subscription or {}).get("couponCode") or ""),
        "购买前积分": str((subscription or {}).get("beforeCredits") if (subscription or {}).get("beforeCredits") is not None else ""),
        "购买后积分": str((subscription or {}).get("afterCredits") if (subscription or {}).get("afterCredits") is not None else ""),
        "积分是否增加": "是" if (subscription or {}).get("creditIncreased") else ("否" if subscription else ""),
        "订阅截图路径": result_screenshot(subscription),
        "订阅截图附件": upload_bitable_attachment(app_token, result_screenshot(subscription)) if result_screenshot(subscription) else [],
        "失败原因": failure_summary(merged),
        "结果JSON路径": str(result_file),
        "原始结果摘要": compact_summary(merged),
    }


def bitable_url(app_token: str, table_id: str) -> str:
    return f"https://feishu.cn/base/{app_token}?table={table_id}"


def list_bitable_records(app_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        data = feishu_request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", params=params)
        payload = data.get("data", {})
        records.extend(payload.get("items") or payload.get("records") or [])
        if not payload.get("has_more"):
            return records
        page_token = payload.get("page_token") or payload.get("next_page_token") or ""
        if not page_token:
            return records


def list_permission_members(app_token: str) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"type": "bitable", "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        data = feishu_request("GET", f"/drive/v1/permissions/{app_token}/members", params=params)
        payload = data.get("data", {})
        members.extend(payload.get("members") or payload.get("items") or [])
        if not payload.get("has_more"):
            return members
        page_token = payload.get("page_token") or payload.get("next_page_token") or ""
        if not page_token:
            return members


def readable_members(app_token: str) -> list[dict[str, Any]]:
    seen = set()
    recipients = []
    for member in list_permission_members(app_token):
        member_type = member.get("member_type")
        member_id = member.get("member_id")
        perm = member.get("perm")
        if not member_type or not member_id or not perm:
            continue
        key = (member_type, member_id)
        if key in seen:
            continue
        seen.add(key)
        recipients.append(member)
    return recipients


def record_field(record: dict[str, Any], name: str) -> Any:
    fields = record.get("fields") or {}
    value = fields.get(name)
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            text = value[0].get("text") or value[0].get("name") or value[0].get("file_token")
            return text or value
        return ", ".join(str(item) for item in value)
    return value


def record_date_text(record: dict[str, Any], name: str) -> str:
    value = record_field(record, name)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return str(value or "")[:10]


def format_record_value(record: dict[str, Any], name: str) -> str:
    value = record_field(record, name)
    if isinstance(value, (int, float)) and name in {"检查日期", "开始时间", "结束时间"}:
        parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return parsed.date().isoformat() if name == "检查日期" else parsed.isoformat().replace("+00:00", "Z")
    return str(value or "-")


def summarize_records(records: list[dict[str, Any]], app_name: str, table_name: str, url: str, target_date: str | None = None) -> str:
    selected_date = target_date or datetime.now(timezone.utc).date().isoformat()
    selected = [record for record in records if record_date_text(record, "检查日期") == selected_date]
    ok_count = sum(1 for record in selected if record_field(record, "整体结果") == "成功")
    fail_count = len(selected) - ok_count
    lines = [
        f"{app_name} / {table_name} 监控汇总",
        f"表格：{url}",
        f"范围：{selected_date}",
        f"记录数：{len(selected)}，成功：{ok_count}，失败：{fail_count}",
        "",
    ]
    if not selected:
        lines.append("当天没有多维表格记录。")
    for record in selected:
        lines.extend(
            [
                f"日期：{format_record_value(record, '检查日期')}",
                f"整体：{format_record_value(record, '整体结果')}",
                f"老用户：{format_record_value(record, '老用户结果')} / {format_record_value(record, '老用户账号')}",
                f"新注册：{format_record_value(record, '新注册结果')} / {format_record_value(record, '新注册账号')}",
                f"订阅：{format_record_value(record, '订阅结果')} / {format_record_value(record, '订阅账号')}",
                f"失败原因：{format_record_value(record, '失败原因')}",
                "",
            ]
        )
    return "\n".join(lines)[:18000]


def receive_id_type(member_type: str) -> str | None:
    normalized = str(member_type or "").lower()
    if normalized in {"email", "open_id", "user_id", "union_id"}:
        return normalized
    if normalized in {"openid", "open-id"}:
        return "open_id"
    if normalized in {"userid", "user-id"}:
        return "user_id"
    if normalized in {"openchat", "chat", "chat_id", "group"}:
        return "chat_id"
    return None


def send_member_text(member: dict[str, Any], text: str) -> dict[str, Any]:
    headers = feishu_headers()
    if not headers:
        return disabled("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    rid_type = receive_id_type(str(member.get("member_type") or ""))
    if not rid_type:
        return disabled(f"unsupported member_type: {member.get('member_type')}")
    response = requests.post(
        f"{BASE_URL}/im/v1/messages",
        params={"receive_id_type": rid_type},
        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
        json={"receive_id": member.get("member_id"), "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        timeout=60,
    )
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    if response.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"发送飞书协作者通知失败: {response.status_code} {data}")
    return data


def send_bitable_summary_to_collaborators(target_date: str | None = None) -> dict[str, Any]:
    bitable = ensure_monitor_bitable()
    url = bitable_url(bitable["app_token"], bitable["table_id"])
    records = list_bitable_records(bitable["app_token"], bitable["table_id"])
    members = readable_members(bitable["app_token"])
    text = summarize_records(records, bitable["app_name"], bitable["table_name"], url, target_date)
    sent = []
    for member in members:
        try:
            response = send_member_text(member, text)
            sent.append({"member": member, "response": response})
        except Exception as error:
            sent.append({"member": member, "error": str(error)})
    return {"url": url, "recordCount": len(records), "memberCount": len(members), "sent": sent}


def append_monitor_record(merged: dict[str, Any], title: str) -> dict[str, Any]:
    if not feishu_headers():
        return disabled("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    bitable = ensure_monitor_bitable()
    fields = adapt_field_values(monitor_record_fields(merged, title, bitable["app_token"]), field_types(bitable["app_token"], bitable["table_id"]))
    data = feishu_request(
        "POST",
        f"/bitable/v1/apps/{bitable['app_token']}/tables/{bitable['table_id']}/records",
        headers={"Content-Type": "application/json; charset=utf-8"},
        json={"fields": fields},
    )
    record = data.get("data", {}).get("record") or data.get("data", {})
    record_id = record.get("record_id") or record.get("id")
    return {
        **bitable,
        "record_id": record_id,
        "url": bitable_url(bitable["app_token"], bitable["table_id"]),
        "fields": fields,
        "response": data,
    }
