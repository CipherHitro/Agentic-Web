import logging
from app.scraper.browser import browser_manager
from app.services.llm_service import get_openai_client
from app.config import settings

from app.scraper.screenshot import capture_screenshot_base64
from app.services.vision_service import analyze_page_screenshot

logger = logging.getLogger(__name__)

async def observe_page(goal: str) -> dict:
    """
    Analyzes the current page and returns a step-by-step action plan
    to achieve the given goal based on what's actually visible.
    """
    page = browser_manager.current_page
    if not page:
        return {"success": False, "message": "No active page"}

    # Collect ALL interactive elements currently in DOM
    elements = await page.evaluate("""() => {
        const getElementText = (el) => {
            let text = (el.innerText || el.value || el.getAttribute('aria-label') || 
                        el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('placeholder') ||
                        el.getAttribute('data-login') || '').trim();
            if (!text) {
                const childImg = el.querySelector('img');
                if (childImg) {
                    text = (childImg.getAttribute('alt') || childImg.getAttribute('title') || childImg.getAttribute('aria-label') || '').trim();
                }
            }
            if (!text) {
                const children = el.querySelectorAll('*');
                for (const child of children) {
                    text = (child.innerText || child.getAttribute('aria-label') || child.getAttribute('alt') || '').trim();
                    if (text) break;
                }
            }
            return text.slice(0, 80);
        };

        const interactive = [];
        
        // Buttons and Summaries
        document.querySelectorAll('button, [role="button"], [type="submit"], summary').forEach(el => {
            if (el.offsetParent === null && el.tagName !== 'SUMMARY') return;
            const text = getElementText(el);
            if (text) {
                interactive.push({
                    type: 'button',
                    text: text,
                    ariaLabel: el.getAttribute('aria-label') || '',
                    id: el.id || '',
                    classes: el.className.slice(0, 60)
                });
            }
        });
        
        // Inputs
        document.querySelectorAll('input:not([type=hidden]), textarea').forEach(el => {
            if (el.offsetParent !== null) {
                interactive.push({
                    type: 'input',
                    inputType: el.type || 'text',
                    placeholder: el.placeholder || '',
                    ariaLabel: el.getAttribute('aria-label') || '',
                    id: el.id || '',
                    name: el.name || ''
                });
            }
        });
        
        // Clickable links that look like actions (not nav)
        document.querySelectorAll('a[href]').forEach(el => {
            const text = getElementText(el);
            if (text && el.offsetParent !== null && text.length < 60) {
                interactive.push({
                    type: 'link',
                    text: text,
                    href: el.href.slice(0, 120)
                });
            }
        });
        
        return interactive.slice(0, 80); // cap at 80 elements
    }""")

    client = get_openai_client()
    prompt = f"""You are analyzing a web page to help an agent accomplish this goal: "{goal}"

Here are all currently visible interactive elements on the page:
{elements}

Based ONLY on what is visible right now, describe:
1. What the agent should click/interact with FIRST to make progress
2. Whether this goal requires opening a dropdown/popup first before the target element appears
3. The exact sequence of steps needed (be specific about element text or aria-labels)

If the needed element is NOT visible yet, say what must be clicked first to reveal it."""

    response = await client.chat.completions.create(
        model=settings.openrouter_model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400
    )

    try:
        base64_img = await capture_screenshot_base64(page)
        vision_guidance = await analyze_page_screenshot(base64_img, goal)
    except Exception as e:
        logger.error(f"Vision guidance inside observe_page failed: {e}")
        vision_guidance = f"Vision guidance failed: {str(e)}"

    return {
        "success": True,
        "visible_elements_count": len(elements),
        "action_plan": response.choices[0].message.content,
        "vision_guidance": vision_guidance,
        "elements_snapshot": elements[:20]  # first 20 for agent reference
    }
