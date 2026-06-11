from typing import Optional

from playwright.async_api import Page


async def navigate(page: Page, url: str):
    try:
        # Use 'domcontentloaded' as it is much faster and less prone to timeout on tracking scripts
        return await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        print(f"Navigation warning for {url} (domcontentloaded): {e}. Falling back to 'commit'...")
        try:
            return await page.goto(url, wait_until="commit", timeout=10000)
        except Exception as fallback_err:
            print(f"Navigation fallback failed for {url}: {fallback_err}")
            # Do not raise exception, we will try to extract whatever has been loaded so far
            return None


async def wait_for_selector(page: Page, selector: Optional[str]) -> None:
    if selector:
        try:
            await page.wait_for_selector(selector, timeout=5000)
        except Exception as e:
            print(f"Warning: Selector '{selector}' not found within timeout: {e}")


async def scroll_to_bottom(page: Page) -> None:
    try:
        await page.evaluate(
            """
            async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 100;
                    const timer = setInterval(() => {
                        const scrollHeight = document.body.scrollHeight;
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if (totalHeight >= scrollHeight) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100);
                });
            }
            """
        )
        await page.wait_for_timeout(1000)
    except Exception as e:
        print(f"Warning: Scroll to bottom failed: {e}")

