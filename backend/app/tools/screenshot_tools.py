import logging
from typing import Any, Dict
from app.scraper.browser import browser_manager
from app.scraper.screenshot import capture_screenshot_for_vision
from app.services.vision_service import analyze_page_screenshot

logger = logging.getLogger(__name__)


async def take_screenshot() -> Dict[str, Any]:
    """
    Capture a screenshot of the current page, analyze it with the vision model,
    and return a visual description of what is on screen so the agent can re-plan.
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {
            "success": False,
            "error": "No active browser session. Call browse_web first."
        }
    
    try:
        current_url = page.url
        page_title = await page.title()

        base64_img, screenshot_path = await capture_screenshot_for_vision(
            page, context_label="take_screenshot"
        )
        try:
            vision_analysis = await analyze_page_screenshot(
                base64_img,
                f"Describe everything visible on this page. What buttons, inputs, links, "
                f"menus, modals, or interactive elements are on screen? "
                f"What is the page about? Current URL: {current_url}",
                screenshot_path=screenshot_path,
            )
        except Exception as ve:
            logger.error(f"Vision analysis in take_screenshot failed: {ve}")
            vision_analysis = f"Vision analysis failed: {str(ve)}"

        return {
            "success": True,
            "current_url": current_url,
            "page_title": page_title,
            "visual_description": vision_analysis,
            "screenshot_path": screenshot_path,
            "message": (
                f"Screenshot captured and analyzed. You are on: {current_url} "
                f"(title: '{page_title}'). See visual_description for what is on screen."
            ),
        }
    except Exception as e:
        logger.error(f"Screenshot failed: {e}")
        return {"success": False, "error": f"Screenshot failed: {str(e)}"}

