import logging
from typing import Any, Dict
from app.scraper.browser import browser_manager
from app.scraper.page_handler import scroll_to_bottom
from app.tools.extraction_tools import extract_clean_content
from app.services.llm_service import get_openai_client
from app.config import settings

logger = logging.getLogger(__name__)


async def click_element(intent: str) -> Dict[str, Any]:
    """
    Click a button or interactive element on the current page by describing
    what you want to click. Works on buttons without href links.
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {
            "success": False,
            "error": "No active browser session. Call browse_web first."
        }

    html = await page.content()
    base_url = page.url

    # Step 1: Ask LLM to pick the best CSS selector for this intent
    client = get_openai_client()

    # Build a list of interactive elements from the page
    elements = await page.evaluate("""
        () => {
            const results = [];
            const selectors = ['button', '[role="button"]', 'input[type="submit"]',
                              'input[type="button"]', 'a', '[onclick]', '[class*="btn"]',
                              '[class*="button"]'];
            for (const sel of selectors) {
                for (const el of document.querySelectorAll(sel)) {
                    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                    const id = el.id ? `#${el.id}` : '';
                    const cls = el.className ? `.${el.className.split(' ')[0]}` : '';
                    if (text) {
                        results.push({
                            tag: el.tagName.toLowerCase(),
                            text: text.slice(0, 80),
                            id: id,
                            cls: cls,
                            selector: id || (el.tagName.toLowerCase() + cls)
                        });
                    }
                    if (results.length >= 50) break;
                }
                if (results.length >= 50) break;
            }
            return results;
        }
    """)

    if not elements:
        return {"success": False, "error": "No clickable elements found on page."}

    formatted = "\n".join(
        f"- Tag: {e['tag']} | Text: '{e['text']}' | Selector: {e['selector']}"
        for e in elements
    )

    prompt = (
        f"User wants to click: \"{intent}\"\n\n"
        f"Available clickable elements:\n{formatted}\n\n"
        "Return ONLY the text content of the best matching element, exactly as shown. "
        "Return 'None' if nothing matches."
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.0,
        )
        target_text = resp.choices[0].message.content.strip().strip("\"'")
    except Exception as e:
        return {"success": False, "error": f"LLM selector pick failed: {e}"}

    if target_text.lower() == "none":
        return {"success": False, "error": f"No element found matching: '{intent}'"}

    # Step 2: Click it using text matching (most reliable cross-site approach)
    try:
        # Try exact text match first
        locator = page.get_by_role("button", name=target_text, exact=False)
        count = await locator.count()

        if count == 0:
            # Fallback: any element with that text
            locator = page.get_by_text(target_text, exact=False).first

        await locator.click(timeout=8000)
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
        await scroll_to_bottom(page)

        title = await page.title()
        html_content = await page.content()
        content, links, nav_links = extract_clean_content(
            html_content, base_url=page.url, max_text_length=8000
        )

        return {
            "success": True,
            "url": page.url,
            "title": title,
            "message": f"Clicked '{target_text}' successfully.",
            "content": content,
            "links": links,
            "navigation_links": nav_links,
        }
    except Exception as e:
        logger.error(f"Click failed for '{target_text}': {e}")
        return {"success": False, "error": f"Click failed: {str(e)}"}
