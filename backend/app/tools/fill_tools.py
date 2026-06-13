import logging
from typing import Any, Dict, Optional
from app.scraper.browser import browser_manager
from app.services.llm_service import get_openai_client
from app.config import settings

logger = logging.getLogger(__name__)


async def fill_form_field(field_description: str, value: str) -> Dict[str, Any]:
    """
    Type text into a form field on the current page.
    Describe the field (e.g. 'search box', 'email input', 'username field').
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {"success": False, "error": "No active browser session."}

    # Get all input/textarea elements
    inputs = await page.evaluate("""
        () => {
            const results = [];
            for (const el of document.querySelectorAll('input, textarea, [contenteditable]')) {
                if (el.type === 'hidden' || el.type === 'submit') continue;
                results.push({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    placeholder: el.placeholder || '',
                    name: el.name || '',
                    id: el.id || '',
                    label: el.getAttribute('aria-label') || ''
                });
                if (results.length >= 30) break;
            }
            return results;
        }
    """)

    if not inputs:
        return {"success": False, "error": "No input fields found on page."}

    client = get_openai_client()
    formatted = "\n".join(
        f"- tag={e['tag']} type={e['type']} placeholder='{e['placeholder']}' "
        f"name='{e['name']}' id='{e['id']}' label='{e['label']}'"
        for e in inputs
    )

    prompt = (
        f"User wants to fill: \"{field_description}\"\n\n"
        f"Available form fields:\n{formatted}\n\n"
        "Return ONLY the 'id' or 'name' attribute of the best matching field. "
        "Prefer id over name. Return 'None' if nothing matches."
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50,
            temperature=0.0,
        )
        field_id = resp.choices[0].message.content.strip().strip("\"'")
    except Exception as e:
        return {"success": False, "error": f"LLM field pick failed: {e}"}

    if field_id.lower() == "none":
        return {"success": False, "error": f"No field matching: '{field_description}'"}

    try:
        # Try by id first, then name
        locator = page.locator(f"#{field_id}")
        if await locator.count() == 0:
            locator = page.locator(f"[name='{field_id}']")
        if await locator.count() == 0:
            # Fallback: placeholder text
            locator = page.get_by_placeholder(field_description, exact=False)

        await locator.first.click()
        await locator.first.fill(value)

        return {
            "success": True,
            "message": f"Filled '{field_description}' with '{value}'."
        }
    except Exception as e:
        logger.error(f"Fill failed: {e}")
        return {"success": False, "error": f"Fill failed: {str(e)}"}
