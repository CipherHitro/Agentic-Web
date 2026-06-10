from typing import Any, Dict, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config import settings
from app.scraper.page_handler import navigate, scroll_to_bottom, wait_for_selector
from app.scraper.screenshot import capture_screenshot_base64
from app.tools.extraction_tools import extract_clean_content


class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._headed: bool = False

    async def start(self, engine: str = "chromium", headless: Optional[bool] = None):
        """Launch browser. headless=False shows the actual browser window."""
        if self._browser:
            return self._browser

        if headless is None:
            headless = not settings.playwright_headed

        self._headed = not headless
        self._playwright = await async_playwright().start()
        browser_type = getattr(self._playwright, engine)

        launch_options: Dict[str, Any] = {"headless": headless}
        if not headless:
            launch_options["args"] = [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ]

        self._browser = await browser_type.launch(**launch_options)
        print(f"Browser ({engine}) launched. Headless: {headless}")
        return self._browser

    async def new_context(self, user_agent: Optional[str] = None) -> BrowserContext:
        """Create isolated browser context with anti-bot measures."""
        if not self._browser:
            await self.start()

        context_options: Dict[str, Any] = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if user_agent:
            context_options["user_agent"] = user_agent

        context = await self._browser.new_context(**context_options)
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            delete window.__playwright;
            delete window.__pw_manual;
            """
        )
        return context

    async def new_page(self, context: BrowserContext) -> Page:
        return await context.new_page()

    async def browse_web(
        self,
        url: str,
        extract_content: bool = True,
        take_screenshot: bool = False,
        scroll_page: bool = True,
        wait_for: Optional[str] = None,
        max_text_length: int = 8000,
    ) -> Dict[str, Any]:
        """Main browsing tool. Returns clean, structured content for the AI."""
        context = None
        try:
            context = await self.new_context()
            page = await self.new_page(context)

            response = await navigate(page, url)
            await wait_for_selector(page, wait_for)

            if scroll_page:
                await scroll_to_bottom(page)

            title = await page.title()
            html_content = await page.content()

            result: Dict[str, Any] = {
                "url": url,
                "title": title,
                "status": response.status if response else None,
                "success": True,
            }

            if extract_content:
                content, links = extract_clean_content(html_content, max_text_length=max_text_length)
                result["content"] = content
                result["links"] = links

            if take_screenshot:
                result["screenshot"] = await capture_screenshot_base64(page)

            return result
        except Exception as e:
            return {
                "url": url,
                "success": False,
                "error": str(e),
            }
        finally:
            if context:
                await context.close()

    async def close(self):
        if self._browser:
            try:
                await self._browser.close()
            except Exception as exc:
                print(f"Browser close skipped: {exc}")
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as exc:
                print(f"Playwright stop skipped: {exc}")
            self._playwright = None
        print("Browser closed")


browser_manager = BrowserManager()
