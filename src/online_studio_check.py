import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright

from .common import (
    ACCOUNTS_FILE,
    API_BASE_URL,
    APP_URL,
    ARTIFACTS_DIR,
    AUTH_BASE_URL,
    DEFAULT_PROMPT,
    LATEST_RESULT_FILE,
    REGISTER_DIR,
    REGISTER_SCRIPT,
    WEB_ORIGIN,
    cleanup_artifacts,
    read_json,
    write_json,
)
from .feishu_client import send_feishu_image, send_feishu_text

PROMPT = os.getenv("ONLINE_CHECK_PROMPT", DEFAULT_PROMPT)


def parse_set_cookie(raw: str) -> dict[str, Any] | None:
    parts = [item.strip() for item in raw.split(";")]
    if not parts or "=" not in parts[0]:
        return None
    name, value = parts[0].split("=", 1)
    cookie: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": ".tripo3d.ai",
        "path": "/",
        "httpOnly": False,
        "secure": True,
        "sameSite": "Lax",
    }
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
        else:
            key, value = part, ""
        lower = key.lower()
        if lower == "domain" and value:
            cookie["domain"] = value if value.startswith(".") else f".{value}"
        elif lower == "path" and value:
            cookie["path"] = value
        elif lower == "samesite" and value:
            cookie["sameSite"] = value
        elif lower == "httponly":
            cookie["httpOnly"] = True
        elif lower == "secure":
            cookie["secure"] = True
    return cookie


def set_cookie_values(response: requests.Response) -> list[str]:
    if hasattr(response.raw, "headers"):
        try:
            return response.raw.headers.get_all("Set-Cookie") or []
        except Exception:
            pass
    raw = response.headers.get("Set-Cookie", "")
    if not raw:
        return []
    return [item.strip() for item in re.split(r",(?=\s*[^;\s]+=)", raw) if item.strip()]


def cookie_header(cookies: list[dict[str, Any]]) -> str:
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)


def find_node_value(flow: dict[str, Any], name: str) -> str:
    for node in flow.get("ui", {}).get("nodes", []):
        attributes = node.get("attributes", {})
        if attributes.get("name") == name:
            return attributes.get("value", "")
    return ""


def password_login_account(email: str | None, password: str | None) -> dict[str, Any]:
    if not email or not password:
        raise RuntimeError("固定账号登录缺少 email 或 password。")
    browser_headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": APP_URL.removesuffix("/workspace"),
        "Referer": f"{APP_URL.removesuffix('/workspace')}/",
    }
    session = requests.Session()
    flow_response = session.get(f"{AUTH_BASE_URL}/self-service/login/browser", headers=browser_headers, allow_redirects=False, timeout=60)
    flow_text = flow_response.text
    try:
        flow = flow_response.json()
    except Exception as exc:
        raise RuntimeError(f"获取 login flow 失败: {flow_response.status_code} {flow_text[:500]}") from exc
    if flow_response.status_code >= 400:
        raise RuntimeError(f"获取 login flow 失败: {flow_response.status_code} {flow_text[:500]}")
    flow_id = flow.get("id")
    flow_cookies = [cookie for cookie in (parse_set_cookie(item) for item in set_cookie_values(flow_response)) if cookie]
    csrf_token = find_node_value(flow, "csrf_token")
    if not flow_id:
        raise RuntimeError("login/browser 未返回 flow id。")
    headers = {
        **browser_headers,
        "Content-Type": "application/json",
    }
    if flow_cookies:
        headers["Cookie"] = cookie_header(flow_cookies)
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    payload = {
        "method": "password",
        "identifier": email,
        "password": password,
    }
    if csrf_token:
        payload["csrf_token"] = csrf_token
    submit_response = session.post(
        f"{AUTH_BASE_URL}/self-service/login?flow={flow_id}",
        headers=headers,
        json=payload,
        allow_redirects=False,
        timeout=60,
    )
    submit_text = submit_response.text
    if submit_response.status_code >= 400 and submit_response.status_code not in (302, 303):
        raise RuntimeError(f"固定账号登录失败: {submit_response.status_code} {submit_text[:1000]}")
    cookies = flow_cookies + [cookie for cookie in (parse_set_cookie(item) for item in set_cookie_values(submit_response)) if cookie]
    session_cookie = next((cookie for cookie in cookies if re.search(r"ory.*session", cookie["name"], re.I)), None)
    if not session_cookie:
        raise RuntimeError("固定账号登录成功但没有返回 Ory session cookie。")
    return {
        "email": email,
        "auth": {
            "oryKratosSession": session_cookie["value"],
            "sessionCookieName": session_cookie["name"],
            "cookies": cookies,
        },
    }


def latest_registered_account() -> dict[str, Any] | None:
    accounts = read_json(ACCOUNTS_FILE, []) or []
    for account in reversed(accounts):
        if account and account.get("status") == "registered" and account.get("email"):
            return account
    return None


def register_online_account() -> dict[str, Any] | None:
    before = {item.get("email") for item in (read_json(ACCOUNTS_FILE, []) or []) if item.get("email")}
    node = shutil.which("node")
    if not node:
        raise RuntimeError("未找到 node，可用账号注册仍依赖 /Users/doro/Desktop/dingyue/register/index.js。")
    args = [
        node,
        str(REGISTER_SCRIPT),
        "--env",
        "production",
        "--single",
        "--accounts-file",
        str(ACCOUNTS_FILE),
        "--email",
        "online-check{{timestamp}}@otpebox.com",
    ]
    result = subprocess.run(
        args,
        cwd=REGISTER_DIR,
        env={
            **os.environ,
            "REGISTER_ENV": "production",
            "AUTH_BASE_URL": AUTH_BASE_URL,
            "API_BASE_URL": API_BASE_URL,
            "WEB_ORIGIN": WEB_ORIGIN,
            "APP_ORIGIN": APP_URL,
            "REGISTER_EMAIL": "online-check{{timestamp}}@otpebox.com",
        },
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"线上注册失败，退出码={result.returncode}")
    accounts = read_json(ACCOUNTS_FILE, []) or []
    for account in accounts:
        if account.get("email") and account.get("email") not in before and account.get("status") == "registered":
            return account
    return latest_registered_account()


def get_existing_account(account_file: str | None = None) -> dict[str, Any]:
    target = Path(account_file or os.getenv("ACCOUNT_FILE") or (ARTIFACTS_DIR.parent / "latest-account.json"))
    account = read_json(target, None)
    if account and account.get("email"):
        return account
    latest = latest_registered_account()
    if latest and latest.get("email"):
        return latest
    raise RuntimeError(f"未找到可用账号，请先运行注册。账号文件: {target}")


async def apply_register_session(context: BrowserContext, account: dict[str, Any] | None) -> bool:
    session = (account or {}).get("auth", {}).get("oryKratosSession")
    if not session:
        return False
    await context.add_cookies([
        {
            "name": (account or {}).get("auth", {}).get("sessionCookieName") or "ory_kratos_session",
            "value": session,
            "domain": ".tripo3d.ai",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    ])
    return True


async def click_first_visible(page: Page, locators: list[Locator], label: str = "") -> bool:
    for locator in locators:
        item = locator.first
        try:
            if await item.is_visible():
                await item.click()
                return True
        except Exception:
            continue
    if label:
        raise RuntimeError(f"没有找到可点击元素：{label}")
    return False


async def fill_prompt(page: Page) -> None:
    inputs = page.locator("textarea, [contenteditable='true'], input[type='text']")
    count = await inputs.count()
    for index in range(count):
        item = inputs.nth(index)
        try:
            if await item.is_visible():
                try:
                    await item.fill(PROMPT)
                except Exception:
                    await item.click()
                    await page.keyboard.type(PROMPT)
                return
        except Exception:
            continue
    raise RuntimeError("没有找到文生模型 prompt 输入框。")


async def dismiss_popups(page: Page) -> None:
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await page.wait_for_timeout(500)
    await click_first_visible(
        page,
        [
            page.get_by_text(re.compile(r"跳过|Skip|稍后再说|Not now|Later", re.I)),
            page.locator("button").filter(has_text=re.compile(r"跳过|Skip|稍后再说|Not now|Later", re.I)),
            page.locator("[aria-label*='close' i], [aria-label*='关闭' i]"),
            page.locator("button, [role='button']").filter(has_text=re.compile(r"^×$|^x$|关闭|Close", re.I)),
        ],
    )
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await page.wait_for_timeout(500)


async def dismiss_experience_modal(page: Page) -> None:
    body = await page.locator("body").inner_text()
    if not re.search(r"帮助我们更好地了解您|解锁 300 积分|你在 3D 建模方面的经验如何", body, re.I):
        return
    await click_first_visible(
        page,
        [
            page.get_by_text(re.compile(r"跳过|Skip", re.I)),
            page.locator("button").filter(has_text=re.compile(r"跳过|Skip", re.I)),
        ],
    )
    await page.wait_for_timeout(1000)


async def ensure_generate_page(page: Page) -> None:
    await page.goto(f"{APP_URL}/generate", wait_until="commit", timeout=60000)
    await page.wait_for_timeout(5000)
    await dismiss_popups(page)
    await dismiss_experience_modal(page)
    body = await page.locator("body").inner_text()
    if re.search(r"Generate Model|生成模型|Describe what you want|描述", body, re.I):
        return
    raise RuntimeError("没有找到 Model 入口或生成高精度模型入口。")


async def enter_model_workspace(page: Page) -> None:
    await dismiss_popups(page)
    await dismiss_experience_modal(page)
    body = await page.locator("body").inner_text()
    if re.search(r"Generate Model|生成模型|Describe what you want|描述", body, re.I):
        return
    clicked = await click_first_visible(
        page,
        [
            page.get_by_text(re.compile(r"生成高精度模型|Generate HD Model", re.I)),
            page.locator("button, div, a").filter(has_text=re.compile(r"生成高精度模型|Generate HD Model", re.I)),
            page.get_by_text(re.compile(r"一键生成任何3D内容|Generate Anything in 3D|Generate", re.I)),
            page.locator("button, div, a").filter(has_text=re.compile(r"一键生成任何3D内容|Generate Anything in 3D|Generate", re.I)),
        ],
    )
    if not clicked:
        await ensure_generate_page(page)
        return
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(3000)
    await dismiss_popups(page)
    await dismiss_experience_modal(page)
    next_body = await page.locator("body").inner_text()
    if not re.search(r"Generate Model|生成模型|Describe what you want|描述", next_body, re.I):
        await ensure_generate_page(page)


def is_generation_response(url: str, method: str) -> bool:
    if method.upper() != "POST" or "api.tripo3d.ai" not in url:
        return False
    return not re.search(r"message/query|survey|profile|whoami|marketing|team/list", url, re.I)


async def capture_generation_response(response, evidence: dict[str, Any]) -> None:
    request = response.request
    url = response.url
    method = request.method
    if not is_generation_response(url, method):
        return
    item: dict[str, Any] = {
        "url": url,
        "method": method,
        "status": response.status,
        "ok": response.ok,
    }
    try:
        item["requestPostData"] = (request.post_data or "")[:1000]
    except Exception:
        pass
    try:
        text = await response.text()
        item["responseText"] = text[:3000]
        try:
            item["responseJson"] = json.loads(text)
        except Exception:
            pass
    except Exception as error:
        item["responseReadError"] = str(error)
    evidence["responses"].append(item)


async def select_text_to_model(page: Page) -> None:
    clicked = await click_first_visible(
        page,
        [
            page.locator('button:has(div[class*="i-tripo\\:pen"])'),
            page.locator('button').filter(has=page.locator('div[class*="i-tripo\\:pen"]')),
            page.get_by_text(re.compile(r"^Text$|^文本$|Text to 3D|文生", re.I)),
            page.locator("button, div, span, [role='tab']").filter(has_text=re.compile(r"^Text$|^文本$|Text to 3D|文生", re.I)),
        ],
    )
    await page.wait_for_timeout(1000)
    body = await page.locator("body").inner_text()
    if clicked and re.search(r"Describe what you want|描述|prompt|Text to 3D|文本|Generate Model|生成模型", body, re.I):
        return
    await click_first_visible(
        page,
        [
            page.locator("button, div, span").filter(has_text=re.compile(r"Ready For A New 3D Model", re.I)),
            page.locator("button, div, span").filter(has_text=re.compile(r"Generate Model|生成模型", re.I)),
        ],
    )
    await page.wait_for_timeout(1000)


async def prompt_visible(page: Page) -> bool:
    body = await page.locator("body").inner_text()
    if PROMPT in body:
        return True
    try:
        value = await page.locator("textarea, [contenteditable='true'], input[type='text']").first.input_value(timeout=1000)
        return PROMPT in value
    except Exception:
        return False


async def run_text_to_model(page: Page) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "prompt": PROMPT,
        "responses": [],
        "submitted": False,
    }

    async def on_response(response) -> None:
        await capture_generation_response(response, evidence)

    page.on("response", lambda response: asyncio.create_task(on_response(response)))
    await page.goto(APP_URL, wait_until="commit", timeout=60000)
    await page.wait_for_timeout(5000)
    await enter_model_workspace(page)
    await click_first_visible(
        page,
        [
            page.get_by_text(re.compile(r"^Model$|^模型$", re.I)),
            page.locator("button, div, span").filter(has_text=re.compile(r"^Model$|^模型$", re.I)),
        ],
    )
    await page.wait_for_timeout(1000)
    await click_first_visible(
        page,
        [
            page.locator("[role='tab'], button").filter(has_text=re.compile(r"text|文本|prompt", re.I)),
        ],
    )
    await page.wait_for_timeout(500)
    await select_text_to_model(page)
    await fill_prompt(page)
    evidence["promptFilled"] = await prompt_visible(page)
    if not evidence["promptFilled"]:
        raise RuntimeError("已尝试填写 prompt，但页面没有检测到 prompt 文本。")
    await click_first_visible(
        page,
        [
            page.locator("button").filter(has_text=re.compile(r"T-Pose", re.I)),
            page.locator("button, div").filter(has_text=re.compile(r"T-Pose", re.I)),
        ],
    )
    await page.wait_for_timeout(500)
    await click_first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"Generate Model|生成模型", re.I)),
            page.locator("button").filter(has_text=re.compile(r"Generate Model|生成模型", re.I)),
            page.locator("div, button").filter(has_text=re.compile(r"Generate Model|生成模型", re.I)),
        ],
        "Generate Model 按钮",
    )
    evidence["submitted"] = True
    evidence["submittedAt"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    await page.wait_for_timeout(12000)
    try:
        await page.wait_for_load_state("networkidle")
    except Exception:
        pass
    body_text = await page.locator("body").inner_text()
    evidence["pageUrlAfterSubmit"] = page.url
    evidence["pageTextMatched"] = bool(re.search(r"generating|生成中|queue|排队|processing|处理中|task|任务|history|历史", body_text, re.I))
    evidence["pageTextSample"] = body_text[:3000]
    evidence["successfulResponses"] = [item for item in evidence["responses"] if item.get("ok")]
    evidence["hasSuccessfulGenerationResponse"] = bool(evidence["successfulResponses"])
    return evidence


async def post_feishu(result: dict[str, Any]) -> None:
    lines = [
        f"线上 Studio 文生模型检查：{'成功' if result.get('ok') else '失败'}",
        f"账号：{result.get('email') or '-'}",
        f"URL：{result.get('url') or '-'}",
        f"截图：{result.get('screenshot') or '-'}",
    ]
    if result.get("error"):
        lines.append(f"错误：{result['error']}")
    result["feishu"] = send_feishu_text("\n".join(lines))
    screenshot = result.get("screenshot")
    if screenshot and Path(screenshot).exists():
        result["feishuImage"] = send_feishu_image(screenshot)


async def run_studio_check(options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    result: dict[str, Any] = {
        "ok": False,
        "label": options.get("label"),
        "mode": options.get("mode"),
        "startedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "prompt": PROMPT,
    }
    browser: Browser | None = None
    playwright = None
    page: Page | None = None
    body_text_path: Path | None = None
    try:
        if options.get("mode") == "password-login":
            account = password_login_account(options.get("email") or os.getenv("ACCOUNT_EMAIL"), options.get("password") or os.getenv("ACCOUNT_PASSWORD"))
        else:
            account = get_existing_account(options.get("account_file")) if options.get("useExistingAccount") else register_online_account()
        if not account or not account.get("email"):
            raise RuntimeError("未获取到可用账号。")
        result["email"] = account["email"]
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=os.getenv("HEADLESS", "true") != "false")
        context = await browser.new_context(viewport={"width": 1440, "height": 1200})
        await apply_register_session(context, account)
        page = await context.new_page()
        result["generationEvidence"] = await run_text_to_model(page)
        if not (
            result["generationEvidence"].get("hasSuccessfulGenerationResponse")
            or result["generationEvidence"].get("pageTextMatched")
        ):
            raise RuntimeError("已点击 Generate，但没有捕获到成功生成接口或生成中页面状态。")
        after = options.get("afterTextToModel")
        if callable(after):
            result["extra"] = await after(page=page, context=context, account=account, result=result)
        result["url"] = page.url
        result_path = ARTIFACTS_DIR / f"online-studio-{int(__import__('time').time() * 1000)}.png"
        await page.screenshot(path=str(result_path), full_page=True)
        result["screenshot"] = str(result_path)
        result["ok"] = True
        await context.close()
    except Exception as error:
        result["error"] = str(error)
        if page:
            result_path = ARTIFACTS_DIR / f"online-studio-failed-{int(__import__('time').time() * 1000)}.png"
            result["screenshot"] = str(result_path)
            try:
                await page.screenshot(path=str(result_path), full_page=True)
            except Exception:
                pass
            try:
                body_text = await page.locator("body").inner_text()
            except Exception:
                body_text = ""
            result["url"] = page.url
            body_text_path = ARTIFACTS_DIR / "latest-body.txt"
            body_text_path.write_text(body_text[:20000], encoding="utf-8")
            result["bodyTextPath"] = str(body_text_path)
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass
        result["finishedAt"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
        if options.get("notifyFeishu", True):
            try:
                await post_feishu(result)
            except Exception as error:
                result["feishuError"] = str(error)
        write_json(Path(options.get("resultFile") or LATEST_RESULT_FILE), result)
        if options.get("cleanupArtifacts", False):
            cleanup_artifacts([result.get("screenshot"), result.get("bodyTextPath")])
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--password-login", action="store_true")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--use-existing-account", action="store_true")
    parser.add_argument("--no-feishu", action="store_true")
    args = parser.parse_args()
    result = await run_studio_check(
        {
            "mode": "password-login" if args.password_login else "registered-account",
            "email": args.email or os.getenv("ACCOUNT_EMAIL"),
            "password": args.password or os.getenv("ACCOUNT_PASSWORD"),
            "useExistingAccount": args.use_existing_account,
            "notifyFeishu": not args.no_feishu,
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
