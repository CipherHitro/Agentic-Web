import logging
import asyncio
import re
from typing import Any, Dict, Optional
from app.scraper.browser import browser_manager
from app.services.llm_service import get_openai_client
from app.config import settings

from app.scraper.screenshot import capture_screenshot_for_vision
from app.services.vision_service import analyze_page_screenshot
from app.scraper.page_state import detect_form_panel_state

logger = logging.getLogger(__name__)

_DESCRIPTION_FIELD_KEYWORDS = ("description", "about", "bio", "summary", "details", "long")


async def _try_fast_panel_fill(
    page, field_description: str, value: str
) -> Optional[Dict[str, Any]]:
    """Fill visible fields in an open edit panel without LLM or vision."""
    panel = await detect_form_panel_state(page)
    if not panel.get("panel_open"):
        return None

    fields = panel.get("fields", [])
    if not fields:
        return None

    desc_lower = field_description.lower()
    wants_description = any(k in desc_lower for k in _DESCRIPTION_FIELD_KEYWORDS)
    target_idx: Optional[int] = None

    if wants_description:
        for f in fields:
            label_lower = (f.get("label") or "").lower()
            if f.get("isTextarea") or any(k in label_lower for k in _DESCRIPTION_FIELD_KEYWORDS):
                target_idx = f["index"]
                break

    textareas = [f for f in fields if f.get("isTextarea")]
    if target_idx is None and len(textareas) == 1:
        target_idx = textareas[0]["index"]
    if target_idx is None and len(fields) == 1:
        target_idx = fields[0]["index"]
    if target_idx is None and textareas:
        target_idx = textareas[0]["index"]

    if target_idx is None:
        return None

    loc = page.locator(f'[data-agent-field="{target_idx}"]')
    if await loc.count() == 0:
        return None

    await loc.first.click()
    await loc.first.fill(value)
    panel_after = await detect_form_panel_state(page)
    print(
        f"✅ [PANEL FILL] Filled field index {target_idx} for '{field_description}' "
        f"(no LLM/vision needed)"
    )
    return {
        "success": True,
        "message": f"Filled '{field_description}' in open edit panel (fast path).",
        "form_panel_state": panel_after,
        "instruction": (
            f"Field filled. Now call click_element(intent=\""
            f"{panel_after.get('submit_buttons', ['Save changes'])[0]}\") to save."
        ),
    }


async def _fill_handle_failure(page, field_description: str, error_msg: str) -> Dict[str, Any]:
    panel = await detect_form_panel_state(page)
    if panel.get("panel_open"):
        print(
            f"⚠️  [FILL FAILED] '{field_description}' but panel is open — "
            f"visible fields: {panel.get('fields')}. Vision skipped; use field labels shown."
        )
        return {
            "success": False,
            "error": error_msg,
            "form_panel_state": panel,
            "vision_guidance": (
                f"Edit panel is open. Visible fields: {panel.get('fields')}. "
                f"Try fill_form_field with the exact label from the list above."
            ),
        }
    try:
        print(f"⚠️  [FILL FAILED] '{field_description}' — calling vision API...")
        safe = re.sub(r"[^\w\-]", "_", field_description)[:40]
        base64_img, screenshot_path = await capture_screenshot_for_vision(
            page, context_label=f"fill_failed_{safe}"
        )
        vision_guidance = await analyze_page_screenshot(
            base64_img,
            f"Fill form field: {field_description}",
            screenshot_path=screenshot_path,
        )
        return {
            "success": False,
            "error": error_msg,
            "vision_guidance": vision_guidance
        }
    except Exception as ve:
        logger.error(f"Vision analysis fallback failed: {ve}")
        return {
            "success": False,
            "error": error_msg,
            "vision_guidance": f"Vision guidance failed: {str(ve)}"
        }


async def pick_best_field(inputs: list, field_description: str) -> Optional[int]:
    client = get_openai_client()
    formatted = "\n".join(
        f"[{i}] id='{e.get('id', '')}' name='{e.get('name', '')}' placeholder='{e.get('placeholder', '')}' "
        f"type='{e.get('type', 'text')}' ariaLabel='{e.get('ariaLabel', '')}' label='{e.get('label', '')}'"
        for i, e in enumerate(inputs)
    )

    prompt = (
        f"User wants to fill: \"{field_description}\"\n\n"
        f"Available form fields:\n{formatted}\n\n"
        "Return ONLY the index number of the best matching field (e.g., 0 or 1 or 2). "
        "Return 'None' if nothing matches."
    )

    try:
        resp = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0,
        )
        best = resp.choices[0].message.content.strip().strip("\"'")
        if best.lower() == "none":
            return None
        return int(best)
    except Exception as e:
        logger.error(f"LLM field pick failed: {e}")
        return None


async def fill_form_field(field_description: str, value: str) -> Dict[str, Any]:
    """
    Type text into a form field on the current page.
    Describe the field (e.g. 'search box', 'email input', 'username field').
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {"success": False, "error": "No active browser session."}

    # Fast path: open edit panel with visible fields — no LLM needed
    fast = await _try_fast_panel_fill(page, field_description, value)
    if fast:
        return fast

    # Try top-level page first, then each child frame
    frames_to_try = [page] + list(page.frames)

    # 1. Attempt to locate field using DOM walk (with retry)
    max_dom_attempts = 2
    for attempt in range(1, max_dom_attempts + 1):
        for context in frames_to_try:
            try:
                inputs = await context.evaluate("""() => {
                    function getAllInputs(root = document) {
                        let inputs = [];
                        const found = root.querySelectorAll(
                            'input:not([type=hidden]):not([type=submit]), textarea, [contenteditable]'
                        );
                        for (const el of found) {
                            inputs.push({ el, root });
                        }
                        const allElements = root.querySelectorAll('*');
                        for (const el of allElements) {
                            if (el.shadowRoot) {
                                inputs.push(...getAllInputs(el.shadowRoot));
                            }
                        }
                        return inputs;
                    }
                    
                    const all = getAllInputs(document);
                    return all.map(({ el, root }, index) => {
                        el.setAttribute('data-fill-id', index.toString());
                        return {
                            fillId: index.toString(),
                            id: el.id || '',
                            name: el.name || '',
                            placeholder: el.placeholder || '',
                            type: el.type || 'text',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            label: (() => {
                                if (el.id) {
                                    const lbl = root.querySelector(`label[for="${el.id}"]`);
                                    if (lbl && lbl.textContent.trim()) return lbl.textContent.trim();
                                }
                                const labelledby = el.getAttribute('aria-labelledby');
                                if (labelledby) {
                                    const text = labelledby.split(' ')
                                        .map(id => root.getElementById(id))
                                        .filter(Boolean)
                                        .map(item => item.textContent.trim())
                                        .filter(Boolean)
                                        .join(' ');
                                    if (text) return text;
                                }
                                let parent = el.parentElement;
                                if (parent) {
                                    const siblingLabel = parent.querySelector('label');
                                    if (siblingLabel && siblingLabel.textContent.trim()) {
                                        return siblingLabel.textContent.trim();
                                    }
                                }
                                let cur = el.parentElement;
                                for (let i = 0; i < 3 && cur; i++) {
                                    const heading = cur.querySelector('[role="heading"], label, legend');
                                    if (heading && heading.textContent.trim()) {
                                        return heading.textContent.trim();
                                    }
                                    cur = cur.parentElement;
                                }
                                return '';
                            })()
                        };
                    });
                }""")

                if not inputs:
                    continue

                best_idx = await pick_best_field(inputs, field_description)
                logger.debug(f"For field_description '{field_description}', inputs: {inputs}, best chosen index: {best_idx}")
                if best_idx is None or best_idx >= len(inputs):
                    continue

                matched_field = inputs[best_idx]
                locator = None

                # Primary selector using the custom attribute we tagged
                if "fillId" in matched_field:
                    loc = context.locator(f'[data-fill-id="{matched_field["fillId"]}"]')
                    if await loc.count() > 0:
                        locator = loc

                # Fallback to standard selector strategies
                if not locator and matched_field.get("id"):
                    safe_id = matched_field["id"].replace('"', '\\"')
                    loc = context.locator(f'[id="{safe_id}"]')
                    if await loc.count() > 0:
                        locator = loc

                if not locator and matched_field.get("name"):
                    safe_name = matched_field["name"].replace('"', '\\"')
                    loc = context.locator(f'[name="{safe_name}"]')
                    if await loc.count() > 0:
                        locator = loc

                if not locator and matched_field.get("ariaLabel"):
                    safe_aria = matched_field["ariaLabel"].replace('"', '\\"')
                    loc = context.locator(f'[aria-label="{safe_aria}"]')
                    if await loc.count() > 0:
                        locator = loc

                if not locator and matched_field.get("placeholder"):
                    loc = context.get_by_placeholder(matched_field["placeholder"])
                    if await loc.count() > 0:
                        locator = loc

                if locator:
                    await locator.first.click()
                    await locator.first.fill(value)
                    return {
                        "success": True,
                        "message": f"Filled '{field_description}' with '{value}' in frame (DOM Walk)."
                    }
            except Exception as e:
                logger.debug(f"Failed trying to fill form field in frame: {e}")
                continue
        
        # If we finished all frames on this attempt and didn't find the field, sleep and try one more time
        if attempt < max_dom_attempts:
            logger.info(f"DOM walk attempt {attempt} did not find field. Retrying after 1s...")
            await asyncio.sleep(1.0)

    # 2. Screenshot fallback
    logger.info("DOM walk failed to locate field. Running screenshot fallback.")
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
        import base64
        base64_image = base64.b64encode(screenshot_bytes).decode('utf-8')
        image_data_url = f"data:image/png;base64,{base64_image}"
        
        # Determine client and model based on settings.nemotron_nvidia
        if settings.nemotron_nvidia:
            from openai import AsyncOpenAI as NvidiaAsyncOpenAI
            client = NvidiaAsyncOpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=settings.nemotron_nvidia
            )
            vision_model = "meta/llama-3.2-11b-vision-instruct"
        else:
            client = get_openai_client()
            vision_model = settings.openrouter_model
            
        prompt = (
            f"We need to fill a form field described as: \"{field_description}\" with value \"{value}\".\n"
            "We could not find the field in the DOM. Based on this screenshot, find the correct input element.\n"
            "Provide the CSS selector or aria-label to target it. "
            "Return ONLY the selector or aria-label (no quotes, no markdown, just the string). "
            "For example: 'input[aria-label=\"Your name\"]' or '[aria-label=\"Your name\"]' or '#email' or 'Your name'. "
            "If you cannot find a matching field, return 'None'."
        )
        
        resp = await client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url}
                        }
                    ]
                }
            ],
            max_tokens=50,
            temperature=0.0,
        )
        selector = resp.choices[0].message.content.strip().strip("\"'")
        logger.info(f"Vision model returned selector: {selector}")
        
        if selector.lower() != "none":
            for context in frames_to_try:
                try:
                    locator = None
                    if selector.startswith("[") or selector.startswith("#") or selector.startswith(".") or " " in selector or ">" in selector:
                        loc = context.locator(selector)
                        if await loc.count() > 0:
                            locator = loc
                            
                    # Attempt robust label/placeholder parsing if it specifies aria-label/name/id
                    extracted_text = None
                    match = re.search(r'aria-label=["\'](.*?)["\']', selector)
                    if match:
                        extracted_text = match.group(1)
                    else:
                        match = re.search(r'\[name=["\'](.*?)["\']\]', selector)
                        if match:
                            extracted_text = match.group(1)
                        else:
                            match = re.search(r'\[id=["\'](.*?)["\']\]', selector)
                            if match:
                                extracted_text = match.group(1)
                    
                    if extracted_text:
                        if not locator:
                            loc = context.get_by_label(extracted_text, exact=False)
                            if await loc.count() > 0:
                                locator = loc
                        if not locator:
                            loc = context.get_by_placeholder(extracted_text, exact=False)
                            if await loc.count() > 0:
                                locator = loc

                    if not locator:
                        safe_sel = selector.replace('"', '\\"')
                        loc = context.locator(f'[aria-label="{safe_sel}"]')
                        if await loc.count() > 0:
                            locator = loc
                            
                    if not locator:
                        safe_sel = selector.replace('"', '\\"')
                        loc = context.locator(f'[name="{safe_sel}"]')
                        if await loc.count() > 0:
                            locator = loc
                            
                    if not locator:
                        safe_sel = selector.replace('"', '\\"')
                        loc = context.locator(f'[id="{safe_sel}"]')
                        if await loc.count() > 0:
                            locator = loc
                            
                    if not locator:
                        # Try plain selector directly as label or placeholder
                        loc = context.get_by_label(selector, exact=False)
                        if await loc.count() > 0:
                            locator = loc
                            
                    if not locator:
                        loc = context.get_by_placeholder(selector, exact=False)
                        if await loc.count() > 0:
                            locator = loc

                    if locator:
                        await locator.first.click()
                        await locator.first.fill(value)
                        return {
                            "success": True,
                            "message": f"Filled '{field_description}' using vision fallback selector: '{selector}'"
                        }
                except Exception as e:
                    logger.debug(f"Vision fallback failed in frame: {e}")
                    continue
    except Exception as e:
        logger.error(f"Vision fallback failed: {e}")

    return await _fill_handle_failure(page, field_description, f"Field not found in any frame: '{field_description}'")

