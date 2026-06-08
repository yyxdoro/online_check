import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from playwright.async_api import Locator, Page

from .common import ARTIFACTS_DIR, API_BASE_URL, APP_URL, LATEST_SUBSCRIPTION_RESULT_FILE, cleanup_artifacts, write_json
from .feishu_client import send_feishu_image, send_feishu_text
from .online_studio_check import run_studio_check

RESULT_FILE = LATEST_SUBSCRIPTION_RESULT_FILE


def normalize_type(value: Any) -> str:
    return str(value or "").strip().lower()


async def click_first_visible(page: Page, locators: list[Locator], label: str = "") -> bool:
    for locator in locators:
        item = locator.first
        try:
            if await item.is_visible():
                await item.scroll_into_view_if_needed()
                try:
                    await item.click()
                except Exception:
                    await item.click(force=True)
                return True
        except Exception:
            continue
    if label:
        raise RuntimeError(f"没有找到可点击元素：{label}")
    return False


def parse_credit_text(text: str) -> int | None:
    numbers = re.findall(r"\d[\d,]*", str(text or ""))
    parsed = [int(item.replace(",", "")) for item in numbers if item.replace(",", "").isdigit()]
    return max(parsed) if parsed else None


async def read_header_credits(page: Page) -> int | None:
    value = await page.evaluate(
        r"""
        () => {
          const visible = (element) => {
            const style = getComputedStyle(element);
            const box = element.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
          };
          const topElements = Array.from(document.querySelectorAll('header *, nav *, [class*="header"] *, [class*="nav"] *, body *'))
            .filter(visible)
            .filter((element) => element.getBoundingClientRect().top < 180 && element.getBoundingClientRect().left > window.innerWidth * 0.55)
            .map((element) => (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim())
            .filter(Boolean);
          const withCoin = topElements.find((text) => /\d/.test(text) && !/upgrade|升级|dcc|bridge|99\+/i.test(text));
          return withCoin || topElements.join(' ');
        }
        """
    )
    return parse_credit_text(value)


async def save_screenshot(page: Page, name: str) -> str:
    file = ARTIFACTS_DIR / name
    await page.screenshot(path=str(file), full_page=True)
    return str(file)


async def open_pricing(page: Page) -> None:
    await page.goto(APP_URL, wait_until="commit", timeout=60000)
    await page.wait_for_timeout(4000)
    await click_first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"升级|Upgrade", re.I)),
            page.locator("button, a, div").filter(has_text=re.compile(r"^升级$|^Upgrade$", re.I)),
            page.locator("button, a, div").filter(has_text=re.compile(r"升级|Upgrade", re.I)),
        ],
        "升级按钮",
    )
    await page.wait_for_timeout(2500)
    await page.wait_for_function(
        "() => /Tripo Studio价格方案|Pricing|价格方案|专业版|Professional/i.test(document.body?.innerText || '')",
        timeout=30000,
    )


async def remove_default_coupon(page: Page) -> None:
    await page.mouse.wheel(0, 900)
    await page.wait_for_timeout(800)
    await click_first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"移除优惠券|Remove coupon|Remove discount", re.I)),
            page.locator("button").filter(has_text=re.compile(r"移除优惠券|Remove coupon|Remove discount", re.I)),
            page.locator("div, button").filter(has_text=re.compile(r"移除优惠券|Remove coupon|Remove discount", re.I)),
        ],
    )
    await page.wait_for_timeout(1000)
    await page.mouse.wheel(0, -900)
    await page.wait_for_timeout(800)


async def click_professional_stripe_subscribe(page: Page) -> None:
    clicked = await page.evaluate(
        r"""
        () => {
          const visible = (element) => {
            const style = getComputedStyle(element);
            const box = element.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && box.width > 0 && box.height > 0;
          };
          const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
          const cards = Array.from(document.querySelectorAll('section, article, div'))
            .filter(visible)
            .filter((element) => /专业版|Professional|Pro\b/i.test(clean(element.innerText || '')) && /订阅|Subscribe/i.test(clean(element.innerText || '')))
            .sort((a, b) => clean(a.innerText || '').length - clean(b.innerText || '').length);
          const card = cards[0];
          if (!card) return false;
          const controls = Array.from(card.querySelectorAll('button, [role="button"], label, div')).filter(visible);
          const stripe = controls.find((element) => /Stripe/i.test(clean(element.innerText || element.textContent || '')));
          if (stripe) stripe.click();
          const subscribe = controls.find((element) => /^订阅$|^Subscribe$/i.test(clean(element.innerText || element.textContent || '')))
            || controls.find((element) => /订阅|Subscribe/i.test(clean(element.innerText || element.textContent || '')));
          if (!subscribe) return false;
          subscribe.scrollIntoView({ block: 'center', inline: 'center' });
          subscribe.click();
          return true;
        }
        """
    )
    if not clicked:
        raise RuntimeError("没有找到专业版 Stripe 订阅按钮。")
    await page.wait_for_url(re.compile(r"checkout\.stripe\.com", re.I), timeout=60000)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)


async def read_stripe_body(page: Page) -> str:
    return await page.locator("body").inner_text(timeout=10000)


def assert_valid_stripe_coupon(body: str, coupon_code: str) -> None:
    if re.search(r"This code is invalid|code is invalid|优惠码无效|促销码无效|代码无效", body, re.I):
        raise RuntimeError(f"Stripe 优惠券无效：{coupon_code}")


def assert_stripe_can_submit(body: str) -> None:
    if re.search(r"PAYMENT METHOD REQUIRED|Payment method required|需要付款方式|请选择付款方式", body, re.I):
        raise RuntimeError("Stripe 还没有可用支付方式，无法提交订阅。")


async def apply_stripe_promotion_code(page: Page, coupon_code: str) -> None:
    if not coupon_code:
        raise RuntimeError("缺少 SUBSCRIPTION_COUPON_CODE，停止真实订阅。")
    await page.wait_for_url(re.compile(r"checkout\.stripe\.com", re.I), timeout=60000)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2500)
    await click_first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"添加促销码|Add promotion code|Promotion code|促销码", re.I)),
            page.locator("button, a, div").filter(has_text=re.compile(r"添加促销码|Add promotion code|Promotion code|促销码", re.I)),
        ],
        "添加促销码",
    )
    await page.wait_for_timeout(1000)
    input_box = page.locator("input[name='promotionCode'], input[placeholder*='促销'], input[placeholder*='Promotion' i], input[autocomplete='off']").first
    await input_box.fill(coupon_code)
    await click_first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"^应用$|^Apply$", re.I)),
            page.locator("button").filter(has_text=re.compile(r"^应用$|^Apply$", re.I)),
        ],
        "应用促销码",
    )
    try:
        await page.wait_for_function(
            "() => /This code is invalid|code is invalid|优惠码无效|促销码无效|代码无效|Total due today|今天应付总额|due today/i.test(document.body?.innerText || '')",
            timeout=20000,
        )
    except Exception:
        pass
    await page.wait_for_timeout(3000)
    assert_valid_stripe_coupon(await read_stripe_body(page), coupon_code)


async def click_stripe_subscribe(page: Page) -> str:
    before = await save_screenshot(page, f"subscription-stripe-before-submit-{int(__import__('time').time() * 1000)}.png")
    body = await read_stripe_body(page)
    (ARTIFACTS_DIR / "subscription-stripe-before-submit.txt").write_text(f"URL: {page.url}\n\n{body[:12000]}\n", encoding="utf-8")
    assert_valid_stripe_coupon(body, os.getenv("SUBSCRIPTION_COUPON_CODE", ""))
    assert_stripe_can_submit(body)
    await click_first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"订阅|Subscribe|支付|Pay", re.I)),
            page.locator("button").filter(has_text=re.compile(r"订阅|Subscribe|支付|Pay", re.I)),
        ],
        "Stripe 订阅按钮",
    )
    await page.wait_for_timeout(5000)
    await page.wait_for_function(
        "() => /studio\\.tripo3d\\.ai/i.test(location.href) && !/checkout\\.stripe\\.com/i.test(location.href)",
        timeout=180000,
    )
    try:
        await page.wait_for_load_state("networkidle")
    except Exception:
        pass
    return before


async def refresh_twice_and_read_credits(page: Page) -> int | None:
    for _ in range(2):
        try:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle")
        except Exception:
            pass
        await page.wait_for_timeout(3000)
    return await read_header_credits(page)


def whoami_jwt(session_cookie: str, cookie_name: str = "ory_kratos_session") -> str:
    response = requests.get(
        f"{os.getenv('AUTH_BASE_URL', 'https://auth.tripo3d.ai').rstrip('/')}/sessions/whoami?tokenize_as=default_jwt",
        headers={"Accept": "application/json", "Cookie": f"{cookie_name}={session_cookie}"},
        timeout=60,
    )
    text = response.text
    data = response.json() if text else {}
    if response.status_code >= 400:
        raise RuntimeError(f"whoami 失败: {response.status_code} {text[:300]}")
    token = data.get("tokenized", "")
    if not token:
        raise RuntimeError("whoami 未返回 tokenized JWT。")
    return token


def fetch_payment_profile(account: dict[str, Any]) -> dict[str, Any]:
    auth = account.get("auth", {})
    session = auth.get("oryKratosSession")
    if not session:
        raise RuntimeError("账号缺少 ory_kratos_session，无法调用 profile/payment。")
    jwt = whoami_jwt(session, auth.get("sessionCookieName") or "ory_kratos_session")
    app_origin = APP_URL.replace("/workspace", "").rstrip("/")
    response = requests.get(
        f"{API_BASE_URL.rstrip('/')}/v2/studio/user/profile/payment",
        headers={
            "Accept": "*/*",
            "Origin": app_origin,
            "Referer": f"{app_origin}/",
            "Authorization": f"Bearer {jwt}",
            "x-tripo-region": "rg1",
        },
        timeout=60,
    )
    text = response.text
    data = response.json() if text else {}
    if response.status_code >= 400:
        raise RuntimeError(f"profile/payment 失败: {response.status_code} {text[:500]}")
    return data.get("data") or data


def wallet_total_credit(profile: dict[str, Any] | None) -> float | None:
    value = ((profile or {}).get("wallet") or {}).get("total_credit")
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


async def post_feishu(result: dict[str, Any]) -> None:
    lines = [
        f"线上 Studio 新注册账号订阅检查：{'成功' if result.get('ok') else '失败'}",
        f"账号：{result.get('email') or '-'}",
        f"优惠券：{result.get('couponCode') or '-'}",
        f"购买前积分：{result.get('beforeCredits') if result.get('beforeCredits') is not None else '-'}",
        f"购买后积分：{result.get('afterCredits') if result.get('afterCredits') is not None else '-'}",
        f"积分是否增加：{'是' if result.get('creditIncreased') else '否'}",
        f"URL：{result.get('url') or '-'}",
        f"截图：{result.get('finalScreenshot') or result.get('screenshot') or '-'}",
    ]
    if result.get("error"):
        lines.append(f"错误：{str(result['error']).splitlines()[0]}")
    result["feishu"] = send_feishu_text("\n".join(lines))
    image = result.get("finalScreenshot") or result.get("screenshot")
    if image and Path(image).exists():
        result["feishuImage"] = send_feishu_image(image)


async def run_subscription_check(notify_feishu: bool = True) -> dict[str, Any]:
    coupon_code = os.getenv("SUBSCRIPTION_COUPON_CODE", "")
    subscription: dict[str, Any] = {
        "ok": False,
        "couponCode": coupon_code,
        "startedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }

    async def after_text_to_model(page: Page, context, account: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        subscription["email"] = account.get("email")
        subscription["beforeUiCredits"] = await read_header_credits(page)
        subscription["beforeScreenshot"] = await save_screenshot(page, f"subscription-before-{int(__import__('time').time() * 1000)}.png")
        try:
            subscription["beforeProfile"] = fetch_payment_profile(account)
        except Exception as error:
            subscription["beforeProfile"] = {"error": str(error)}
        subscription["beforeCredits"] = wallet_total_credit(subscription.get("beforeProfile"))
        await open_pricing(page)
        await remove_default_coupon(page)
        subscription["pricingScreenshot"] = await save_screenshot(page, f"subscription-pricing-{int(__import__('time').time() * 1000)}.png")
        await click_professional_stripe_subscribe(page)
        await apply_stripe_promotion_code(page, coupon_code)
        subscription["stripeScreenshot"] = await save_screenshot(page, f"subscription-stripe-coupon-{int(__import__('time').time() * 1000)}.png")
        subscription["stripeBeforeSubmitScreenshot"] = await click_stripe_subscribe(page)
        subscription["afterUiCredits"] = await refresh_twice_and_read_credits(page)
        try:
            subscription["afterProfile"] = fetch_payment_profile(account)
        except Exception as error:
            subscription["afterProfile"] = {"error": str(error)}
        subscription["afterCredits"] = wallet_total_credit(subscription.get("afterProfile"))
        subscription["finalScreenshot"] = await save_screenshot(page, f"subscription-after-{int(__import__('time').time() * 1000)}.png")
        before_wallet = subscription.get("beforeCredits")
        after_wallet = subscription.get("afterCredits")
        if before_wallet is None or after_wallet is None:
            raise RuntimeError(
                f"profile/payment 未返回可用积分：before={before_wallet}, after={after_wallet}, "
                f"beforeUi={subscription.get('beforeUiCredits')}, afterUi={subscription.get('afterUiCredits')}"
            )
        subscription["creditIncreased"] = after_wallet > before_wallet
        subscription["ok"] = bool(subscription["creditIncreased"])
        if not subscription["ok"]:
            raise RuntimeError(f"订阅后积分未增加：before={before_wallet}, after={after_wallet}")
        return subscription

    result = await run_studio_check(
        {
            "mode": "registered-account",
            "label": "新注册账号订阅校验",
            "notifyFeishu": False,
            "resultFile": str(ARTIFACTS_DIR / "subscription-studio-result.json"),
            "afterTextToModel": after_text_to_model,
        }
    )
    subscription.update(
        {
            "ok": bool(result.get("ok") and subscription.get("ok")),
            "email": result.get("email") or subscription.get("email"),
            "url": result.get("url"),
            "studioResult": result,
            "finishedAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "screenshot": result.get("screenshot"),
        }
    )
    if notify_feishu:
        try:
            await post_feishu(subscription)
        except Exception as error:
            subscription["feishuError"] = str(error)
    write_json(RESULT_FILE, subscription)
    return subscription


async def main() -> None:
    subscription = await run_subscription_check()
    print(json.dumps(subscription, ensure_ascii=False, indent=2))
    if not subscription.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
