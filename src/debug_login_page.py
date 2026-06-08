import asyncio
import os

from playwright.async_api import async_playwright

from .common import APP_URL, LATEST_ACCOUNT_FILE, read_json
from .online_studio_check import apply_register_session


async def main() -> None:
    account_file = os.getenv("ACCOUNT_FILE") or str(LATEST_ACCOUNT_FILE)
    account = read_json(__import__("pathlib").Path(account_file), None)
    if not account or not account.get("email"):
        raise RuntimeError(f"账号文件无效: {account_file}")
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=os.getenv("HEADLESS", "true") != "false")
    context = await browser.new_context(viewport={"width": 1440, "height": 1200})
    await apply_register_session(context, account)
    page = await context.new_page()
    await page.goto(APP_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    print(await page.locator("body").inner_text())
    await browser.close()
    await playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
