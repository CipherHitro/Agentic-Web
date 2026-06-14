import logging
from typing import Any, Dict
from app.scraper.browser import browser_manager
from app.tools.extraction_tools import extract_clean_content

logger = logging.getLogger(__name__)


async def scroll(direction: str = "down") -> Dict[str, Any]:
    """
    Scroll the current page up, down, to top, or to bottom.
    Returns the newly visible/extracted content after scrolling.
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {
            "success": False,
            "error": "No active browser session. Call browse_web first."
        }
    
    try:
        direction_lower = direction.lower()
        if direction_lower == "down":
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
        elif direction_lower == "up":
            await page.evaluate("window.scrollBy(0, -window.innerHeight)")
        elif direction_lower == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction_lower == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            return {
                "success": False,
                "error": f"Invalid direction: {direction}. Use 'up', 'down', 'top', or 'bottom'.",
            }
            
        await page.wait_for_timeout(1000)
        
        html_content = await page.content()
        content, links, nav_links = extract_clean_content(
            html_content, base_url=page.url, max_text_length=8000
        )
        
        return {
            "success": True,
            "message": f"Scrolled {direction} successfully.",
            "url": page.url,
            "content": content,
            "links": links,
            "navigation_links": nav_links,
        }
    except Exception as e:
        logger.error(f"Scroll failed: {e}")
        return {"success": False, "error": f"Scroll failed: {str(e)}"}
