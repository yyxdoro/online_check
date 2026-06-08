import json
import os
from pathlib import Path
from typing import Any

import requests

from .common import ROOT, load_env_file

load_env_file(ROOT / ".env")

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
CHAT_ID = os.getenv("FEISHU_CHAT_ID", "")
BASE_URL = "https://open.feishu.cn/open-apis"
_token_cache: dict[str, Any] = {}


def disabled(reason: str) -> dict[str, Any]:
    return {"skipped": True, "reason": reason}


def is_feishu_configured() -> bool:
    return bool(APP_ID and APP_SECRET and CHAT_ID)


def get_tenant_access_token() -> str | None:
    if not APP_ID or not APP_SECRET:
        return None
    response = requests.post(
        f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=30,
    )
    data = response.json()
    if response.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: {response.status_code} {data}")
    token = data.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
    return token


def feishu_headers() -> dict[str, str] | None:
    token = _token_cache.get("tenant_access_token")
    if not token:
        token = get_tenant_access_token()
        if not token:
            return None
        _token_cache["tenant_access_token"] = token
    return {"Authorization": f"Bearer {token}"}


def send_feishu_text(text: str) -> dict[str, Any]:
    headers = feishu_headers()
    if not headers:
        return disabled("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    if not CHAT_ID:
        return disabled("missing FEISHU_CHAT_ID")
    response = requests.post(
        f"{BASE_URL}/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
        json={"receive_id": CHAT_ID, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        timeout=30,
    )
    data = response.json()
    if response.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"发送飞书文本失败: {response.status_code} {data}")
    return data


def upload_feishu_image(file: str | Path) -> str | dict[str, Any]:
    headers = feishu_headers()
    if not headers:
        return disabled("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    path = Path(file)
    with path.open("rb") as image:
        response = requests.post(
            f"{BASE_URL}/im/v1/images",
            headers=headers,
            data={"image_type": "message"},
            files={"image": (path.name, image, "image/png")},
            timeout=60,
        )
    data = response.json()
    if response.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"上传飞书图片失败: {response.status_code} {data}")
    image_key = data.get("data", {}).get("image_key") or data.get("image_key")
    if not image_key:
        raise RuntimeError(f"上传飞书图片失败: {data}")
    return image_key


def send_feishu_image_key(image_key: str) -> dict[str, Any]:
    headers = feishu_headers()
    if not headers:
        return disabled("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    if not CHAT_ID:
        return disabled("missing FEISHU_CHAT_ID")
    response = requests.post(
        f"{BASE_URL}/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
        json={"receive_id": CHAT_ID, "msg_type": "image", "content": json.dumps({"image_key": image_key})},
        timeout=30,
    )
    data = response.json()
    if response.status_code >= 400 or data.get("code") not in (0, None):
        raise RuntimeError(f"发送飞书图片失败: {response.status_code} {data}")
    return data


def send_feishu_image(file: str | Path) -> dict[str, Any]:
    if not APP_ID or not APP_SECRET:
        return disabled("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    if not CHAT_ID:
        return disabled("missing FEISHU_CHAT_ID")
    path = Path(file)
    if not path.exists():
        return disabled(f"image not found: {path}")
    image_key = upload_feishu_image(path)
    if isinstance(image_key, dict):
        return image_key
    response = send_feishu_image_key(image_key)
    return {"imageKey": image_key, "response": response}


def start_feishu_ws_client() -> None:
    raise RuntimeError("Python 版本暂未实现飞书 WebSocket 长连接；检查和通知不依赖该功能。")
