import logging
import re

from app.scraper.browser import browser_manager
from app.scraper.dom_scanner import SCAN_INTERACTIVE_ELEMENTS_JS
from app.scraper.page_handler import scroll_to_top
from app.scraper.page_state import detect_form_panel_state
from app.services.llm_service import get_openai_client
from app.config import settings

from app.scraper.screenshot import capture_screenshot_for_vision
from app.services.vision_service import analyze_page_screenshot

logger = logging.getLogger(__name__)

_UI_DISCOVERY_KEYWORDS = (
    "edit", "settings", "button", "save", "description", "about", "gear",
    "pencil", "modal", "form", "input", "submit", "configure", "update",
)


def _goal_needs_top_of_page(goal: str) -> bool:
    goal_lower = goal.lower()
    return any(kw in goal_lower for kw in _UI_DISCOVERY_KEYWORDS)


def _parse_next_action(action_plan: str) -> dict:
    """Extract structured next-action hints from the LLM action plan."""
    result = {"tool": None, "intent": None, "url": None}
    tool_match = re.search(
        r"NEXT_TOOL:\s*(click_element|fill_form_field|navigate_page|browse_web|scroll|go_back)",
        action_plan,
        re.IGNORECASE,
    )
    if tool_match:
        result["tool"] = tool_match.group(1).lower()
    intent_match = re.search(r'NEXT_INTENT:\s*["\']?([^"\'\n]+)["\']?', action_plan, re.IGNORECASE)
    if intent_match:
        result["intent"] = intent_match.group(1).strip()
    url_match = re.search(r"NEXT_URL:\s*(\S+)", action_plan, re.IGNORECASE)
    if url_match:
        result["url"] = url_match.group(1).strip()
    return result


async def observe_page(goal: str) -> dict:
    """
    Analyzes the current page and returns a step-by-step action plan
    to achieve the given goal based on what's actually visible.
    Also includes current_url so the agent can detect if it is on the wrong page.
    """
    page = browser_manager.current_page
    if not page:
        return {"success": False, "message": "No active page"}

    current_url = page.url
    page_title = await page.title()

    scroll_y_before = await page.evaluate("() => window.scrollY")
    scrolled_to_top = False
    if _goal_needs_top_of_page(goal) and scroll_y_before > 100:
        await scroll_to_top(page)
        scrolled_to_top = True

    # If edit panel is already open, skip vision/LLM — tell agent to fill+save
    panel = await detect_form_panel_state(page)
    if panel.get("panel_open"):
        print("📋 [PANEL OPEN] Skipping observe vision — edit panel already visible")
        rec_tool = panel.get("recommended_next") or "fill_form_field"
        rec_intent = panel.get("recommended_intent") or "description"
        return {
            "success": True,
            "current_url": current_url,
            "page_title": page_title,
            "panel_already_open": True,
            "skipped_vision": True,
            "form_panel_state": panel,
            "action_plan": (
                f"Edit panel is ALREADY OPEN. Visible fields: {panel.get('fields')}. "
                f"Save buttons: {panel.get('submit_buttons')}. "
                "Do NOT click the edit trigger again — it will close the panel. "
                "Fill empty fields, then click Save."
            ),
            "next_action": {"tool": rec_tool, "intent": rec_intent, "url": None},
            "vision_guidance": None,
            "instruction": (
                f"Panel open. Call {rec_tool} next. Do NOT call observe_page again."
            ),
        }

    # Collect ALL interactive elements currently in DOM
    elements = await page.evaluate(SCAN_INTERACTIVE_ELEMENTS_JS)

    github_hint = ""
    if "github.com" in current_url:
        github_hint = (
            "\n\nGITHUB HINT: On repo pages, description/metadata edit controls are "
            "icon-only buttons in the right-sidebar 'About' section near the TOP — "
            "NOT in the README at the bottom. These buttons are identified by aria-label "
            "(not visible text). Scroll to top before looking for them."
        )

    client = get_openai_client()
    prompt = f"""You are analyzing a web page to help an agent accomplish this goal: "{goal}"

Current page URL: {current_url}
Page title: {page_title}
Scroll position before observe: y={scroll_y_before}px. Scrolled to top: {scrolled_to_top}.
{github_hint}

Here are all currently visible interactive elements on the page:
{elements}

Based ONLY on what is visible right now, answer:
1. Is this the CORRECT page for accomplishing the goal? If NOT, say so and recommend go_back() or browse_web().
2. What the agent should click/interact with FIRST to make progress (if on the right page).
3. Whether the goal requires opening a dropdown/popup first before the target element appears.
4. The exact sequence of steps needed (be specific about element text or aria-labels).
   For icon-only buttons, use the aria-label value as the click_element intent.

If the needed element is NOT visible yet, say what must be clicked first to reveal it.
If the agent previously scrolled down to read content, remind them controls may be above the fold — scroll up.

At the END of your response, include this exact block:
NEXT_TOOL: <click_element|fill_form_field|navigate_page|browse_web|scroll|go_back>
NEXT_INTENT: "<exact click/fill intent or scroll direction>"
NEXT_URL: <direct URL if browse_web is better, else none>"""

    response = await client.chat.completions.create(
        model=settings.openrouter_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
    )
    action_plan = response.choices[0].message.content
    next_action = _parse_next_action(action_plan)

    screenshot_path = None
    try:
        safe = re.sub(r"[^\w\-]", "_", goal)[:40]
        base64_img, screenshot_path = await capture_screenshot_for_vision(
            page, context_label=f"observe_{safe}"
        )
        vision_guidance = await analyze_page_screenshot(
            base64_img, goal, screenshot_path=screenshot_path
        )
    except Exception as e:
        logger.error(f"Vision guidance inside observe_page failed: {e}")
        vision_guidance = f"Vision guidance failed: {str(e)}"

    return {
        "success": True,
        "current_url": current_url,
        "page_title": page_title,
        "scroll_y_before": scroll_y_before,
        "scrolled_to_top": scrolled_to_top,
        "visible_elements_count": len(elements),
        "action_plan": action_plan,
        "next_action": next_action,
        "vision_guidance": vision_guidance,
        "screenshot_path": screenshot_path,
        "elements_snapshot": elements[:20],
        "instruction": (
            "observe_page is complete. You MUST now call the tool specified in next_action "
            "(usually click_element with the NEXT_INTENT). Do NOT call observe_page again "
            "without acting first."
        ),
    }
