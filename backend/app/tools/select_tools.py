import asyncio
import logging
from typing import Any, Dict
from app.scraper.browser import browser_manager
from app.services.llm_service import get_openai_client
from app.config import settings

logger = logging.getLogger(__name__)

_SCAN_AND_TAG_JS = """
() => {
    const groups = [];
    let groupIndex = 0;

    // --- ARIA-based (Google Forms) ---
    const allListItems = Array.from(document.querySelectorAll('[role="listitem"]'));
    const ariaItems = allListItems.filter(item => {
        let p = item.parentElement;
        while (p) {
            if (p.getAttribute && p.getAttribute('role') === 'listitem') return false;
            p = p.parentElement;
        }
        return true;
    });
    for (const item of ariaItems) {
        const heading = item.querySelector('[role="heading"]');
        const question = heading ? heading.textContent.trim() : item.textContent.trim().slice(0, 100);
        const optionEls = item.querySelectorAll('[role="radio"], [role="checkbox"], [role="option"], option');
        if (optionEls.length === 0) continue;

        const options = [];
        optionEls.forEach((el, i) => {
            const label = el.getAttribute('aria-label') || el.getAttribute('data-value') || el.textContent.trim();
            const role = el.getAttribute('role') || el.type || el.tagName.toLowerCase();
            const selectId = `gform-${groupIndex}-${i}`;
            el.setAttribute('data-select-id', selectId);
            options.push({ selectId, label, role, checked: el.getAttribute('aria-checked') === 'true' || el.selected });
        });
        groups.push({ question, options });
        groupIndex++;
    }
    if (groups.length > 0) return groups;

    // --- Native HTML radio/checkbox fallback ---
    const native = {};
    document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach((el, i) => {
        const name = el.name || ('unnamed_' + i);
        if (!native[name]) native[name] = { question: name, options: [] };
        let label = '';
        if (el.id) {
            const lbl = document.querySelector(`label[for="${el.id}"]`);
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parentLabel = el.closest('label');
            if (parentLabel) label = parentLabel.textContent.trim();
        }
        const selectId = `native-${name}-${i}`;
        el.setAttribute('data-select-id', selectId);
        native[name].options.push({ selectId, label: label || el.value || '', role: el.type, checked: el.checked });
    });
    return Object.values(native);
}
"""


async def select_form_option(question: str, option: str) -> Dict[str, Any]:
    """
    Select a radio button, checkbox, linear-scale value, star/rating value,
    or dropdown option on the current page.
    question = the question text this option belongs to.
    option = the exact option label to select (e.g. "1", "Instagram", "5").
    For checkbox questions with multiple required answers, call this once
    per desired option. Do NOT use fill_form_field for radio/checkbox/rating.
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {"success": False, "error": "No active browser session."}

    # Step 1: Pre-emptively click listboxes (dropdowns) matching the question to render their option list
    for frame in [page] + list(page.frames):
        try:
            expanded = await frame.evaluate("""
                (questionText) => {
                    const ariaItems = document.querySelectorAll('[role="listitem"]');
                    let clicked = false;
                    for (const item of ariaItems) {
                        const heading = item.querySelector('[role="heading"]');
                        const question = heading ? heading.textContent.trim() : item.textContent.trim().slice(0, 100);
                        if (question.toLowerCase().includes(questionText.toLowerCase())) {
                            const listbox = item.querySelector('[role="listbox"]');
                            if (listbox && listbox.getAttribute('aria-expanded') !== 'true') {
                                listbox.click();
                                clicked = true;
                            }
                        }
                    }
                    return clicked;
                }
            """, question)
            if expanded:
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.debug(f"Pre-clicking listbox failed: {e}")

    # Step 2: Scan and matching options
    for frame in [page] + list(page.frames):
        try:
            groups = await frame.evaluate(_SCAN_AND_TAG_JS)
            if not groups:
                continue

            formatted = []
            for g in groups:
                opts = ", ".join(
                    f"'{o['label']}' (id={o['selectId']}, checked={o['checked']})"
                    for o in g["options"]
                )
                formatted.append(f"Question: \"{g['question']}\"\n  Options: {opts}")
            formatted_str = "\n\n".join(formatted)

            client = get_openai_client()
            prompt = (
                f"The user wants to select \"{option}\" for the question: \"{question}\"\n\n"
                f"Form questions and their options:\n\n{formatted_str}\n\n"
                "Return ONLY the 'id' value of the option to click that best matches "
                "BOTH the target question AND the desired option text. "
                "Return 'None' if no good match exists in this list."
            )

            resp = await client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
                temperature=0.0,
            )
            select_id = resp.choices[0].message.content.strip().strip("\"'")

            if select_id.lower() == "none":
                continue

            # Locate the matched option's metadata (role + current checked state)
            matched_option = None
            for g in groups:
                for o in g["options"]:
                    if o["selectId"] == select_id:
                        matched_option = o
                        break
                if matched_option:
                    break

            locator = frame.locator(f'[data-select-id="{select_id}"]')
            if await locator.count() == 0:
                continue

            # --- CRITICAL: idempotency guard for checkboxes ---
            # Clicking an ALREADY-CHECKED checkbox toggles it OFF.
            # If it's already in the desired state, do nothing.
            if matched_option and matched_option.get("role") == "checkbox" and matched_option.get("checked"):
                return {
                    "success": True,
                    "message": f"'{option}' for question '{question}' was already selected (no-op)."
                }

            # Native <option> handling
            element_tag = await locator.first.evaluate("el => el.tagName.toLowerCase()")
            if element_tag == "option":
                parent_select = locator.first.locator("xpath=./ancestor::select")
                if await parent_select.count() > 0:
                    val = await locator.first.get_attribute("value") or await locator.first.text_content()
                    await parent_select.first.select_option(value=val)
                    return {
                        "success": True,
                        "message": f"Selected '{option}' for question '{question}' (native select)."
                    }

            try:
                await locator.first.click(timeout=3000)
            except Exception as click_err:
                logger.warning(f"Standard click failed, attempting JS click: {click_err}")
                await locator.first.evaluate("el => el.click()")

            # --- Verify the click actually registered ---
            await asyncio.sleep(0.2)
            if matched_option and matched_option.get("role") in ("checkbox", "radio"):
                new_state = await locator.first.get_attribute("aria-checked")
                if new_state != "true":
                    # Retry with a raw pointer-event sequence (some custom
                    # widgets ignore Playwright's synthetic .click())
                    try:
                        await locator.first.dispatch_event("pointerdown")
                        await locator.first.dispatch_event("pointerup")
                        await locator.first.dispatch_event("click")
                        await asyncio.sleep(0.2)
                        new_state = await locator.first.get_attribute("aria-checked")
                    except Exception as e:
                        logger.debug(f"Pointer-event fallback failed: {e}")

                if new_state != "true":
                    return {
                        "success": False,
                        "error": (
                            f"Clicked '{option}' for question '{question}' but aria-checked "
                            f"did not become 'true'. The widget may not respond to synthetic "
                            f"clicks here."
                        )
                    }

            return {
                "success": True,
                "message": f"Selected '{option}' for question '{question}'."
            }
        except Exception as e:
            logger.debug(f"select_form_option failed in frame: {e}")
            continue

    # Step 3: Vision fallback — locate the option by pixel coordinates
    try:
        import base64
        screenshot_bytes = await page.screenshot(full_page=False)
        base64_image = base64.b64encode(screenshot_bytes).decode("utf-8")
        image_data_url = f"data:image/png;base64,{base64_image}"

        if settings.nemotron_nvidia:
            from openai import AsyncOpenAI as NvidiaAsyncOpenAI
            vclient = NvidiaAsyncOpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=settings.nemotron_nvidia,
            )
            vision_model = "meta/llama-3.2-11b-vision-instruct"
        else:
            vclient = get_openai_client()
            vision_model = settings.openrouter_model

        viewport = page.viewport_size or {"width": 1920, "height": 1080}
        prompt = (
            f"This is a screenshot of a form ({viewport['width']}x{viewport['height']}px). "
            f"Find the radio button, checkbox, or rating icon for the question "
            f"\"{question}\" that corresponds to the option \"{option}\". "
            "Return ONLY pixel coordinates as 'x,y' (e.g. '420,310') for the center "
            "of that clickable element. Return 'None' if you cannot find it."
        )

        resp = await vclient.chat.completions.create(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }],
            max_tokens=20,
            temperature=0.0,
        )
        coords_str = resp.choices[0].message.content.strip().strip("\"'")

        if coords_str.lower() != "none" and "," in coords_str:
            x_str, y_str = coords_str.split(",")
            x, y = float(x_str.strip()), float(y_str.strip())
            await page.mouse.click(x, y)
            await asyncio.sleep(0.3)
            return {
                "success": True,
                "message": f"Selected '{option}' for question '{question}' via vision fallback at ({x}, {y})."
            }
    except Exception as e:
        logger.error(f"Vision fallback for select_form_option failed: {e}")

    return {
        "success": False,
        "error": f"Could not find/select option '{option}' for question '{question}'. "
                 f"Call read_form_fields() to re-check available questions and options."
    }
