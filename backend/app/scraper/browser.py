import asyncio
import logging
from typing import Any, Dict, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config import settings
from app.scraper.page_handler import navigate, scroll_to_bottom, wait_for_selector
from app.scraper.screenshot import capture_screenshot_base64
from app.tools.extraction_tools import extract_clean_content

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._headed: bool = False

    async def start(self, engine: str = "chromium", headless: Optional[bool] = None):
        """Launch browser. headless=False shows the actual browser window."""
        if self._browser:
            if self._browser.is_connected():
                return self._browser
            logger.warning("Browser disconnected or crashed. Cleaning up and restarting...")
            await self.close()

        if headless is None:
            headless = not settings.playwright_headed

        self._headed = not headless
        self._playwright = await async_playwright().start()
        browser_type = getattr(self._playwright, engine)

        launch_options: Dict[str, Any] = {"headless": headless}
        if not headless:
            launch_options["slow_mo"] = 500
            launch_options["args"] = [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ]

        self._browser = await browser_type.launch(**launch_options)
        logger.info(f"Browser ({engine}) launched. Headless: {headless}")
        return self._browser

    async def new_context(self, user_agent: Optional[str] = None) -> BrowserContext:
        """Create isolated browser context with anti-bot measures."""
        if not self._browser or not self._browser.is_connected():
            await self.start(headless=not self._headed if self._browser else None)

        context_options: Dict[str, Any] = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }
        if user_agent:
            context_options["user_agent"] = user_agent

        try:
            context = await self._browser.new_context(**context_options)
        except Exception as e:
            logger.error(f"Failed to create browser context: {e}. Attempting browser reconnect...")
            await self.close()
            await self.start(headless=not self._headed)
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
        """Main browsing tool. Returns clean, structured content for the AI with retries on failure."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        retries = 3
        backoff_factors = [1, 2, 4]
        last_error = None

        for attempt in range(retries + 1):
            context = None
            try:
                context = await self.new_context()
                page = await self.new_page(context)

                response = await navigate(page, url)
                if response is None:
                    raise RuntimeError("Failed to load page response (navigation timed out or network error).")

                if response.status >= 500:
                    raise RuntimeError(f"Server returned status code {response.status}")

                await wait_for_selector(page, wait_for)

                if scroll_page:
                    await scroll_to_bottom(page)

                title = await page.title()
                html_content = await page.content()

                result: Dict[str, Any] = {
                    "url": url,
                    "title": title,
                    "status": response.status,
                    "success": True,
                }

                if extract_content:
                    content, links = extract_clean_content(html_content, max_text_length=max_text_length)
                    result["content"] = content
                    result["links"] = links

                if take_screenshot:
                    result["screenshot"] = await capture_screenshot_base64(page)

                if self._headed:
                    logger.info("Headed mode: sleeping 3 seconds before closing page for demo visibility")
                    await asyncio.sleep(3.0)

                return result

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Attempt {attempt + 1} failed browsing {url}: {e}")
                if attempt < retries:
                    sleep_time = backoff_factors[attempt]
                    logger.info(f"Retrying in {sleep_time}s...")
                    await asyncio.sleep(sleep_time)
            finally:
                if context:
                    await context.close()

        return {
            "url": url,
            "success": False,
            "error": f"Failed after {retries + 1} attempts. Last error: {last_error}",
        }




    async def close(self):
        if self._browser:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.warning(f"Browser close skipped: {exc}")
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.warning(f"Playwright stop skipped: {exc}")
            self._playwright = None
        logger.info("Browser closed")


browser_manager = BrowserManager()
