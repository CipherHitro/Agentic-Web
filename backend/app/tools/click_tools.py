import logging
import re
from typing import Any, Dict, List, Optional

from app.scraper.browser import browser_manager
from app.scraper.dom_scanner import (
    MATCH_ELEMENT_BY_TEXT_JS,
    SCAN_INTERACTIVE_ELEMENTS_JS,
)
from app.scraper.page_handler import scroll_to_bottom, scroll_to_top
from app.scraper.page_state import detect_form_panel_state
from app.tools.extraction_tools import extract_clean_content
from app.services.llm_service import get_openai_client
from app.config import settings

from app.scraper.screenshot import capture_screenshot_for_vision
from app.services.vision_service import analyze_page_screenshot, vision_click_coordinates

logger = logging.getLogger(__name__)

_EDIT_TRIGGER_KEYWORDS = (
    "edit", "description", "about", "pencil", "configure", "metadata",
)
_SUBMIT_KEYWORDS = ("save", "submit", "apply", "confirm", "done", "update")


def _is_edit_trigger_intent(intent: str) -> bool:
    intent_lower = intent.lower()
    if _is_submit_intent(intent):
        return False
    return any(kw in intent_lower for kw in _EDIT_TRIGGER_KEYWORDS)


def _is_submit_intent(intent: str) -> bool:
    intent_lower = intent.lower()
    return any(kw in intent_lower for kw in _SUBMIT_KEYWORDS)


def _is_edit_intent(intent: str) -> bool:
    """Backward compat — scroll-to-top for edit OR save in open panels."""
    return _is_edit_trigger_intent(intent) or _is_submit_intent(intent)


def _extract_click_keywords(intent: str) -> List[str]:
    """Build keyword list for aria-label fuzzy matching from user intent."""
    words = re.findall(r"[a-z]{3,}", intent.lower())
    related: List[str] = []
    intent_lower = intent.lower()
    if any(k in intent_lower for k in ("description", "about", "metadata", "repo")):
        related.extend(["edit", "repository", "metadata", "about", "description"])
    if "setting" in intent_lower:
        related.extend(["settings", "configure", "metadata"])
    return list(dict.fromkeys(words + related))


async def _build_click_success_response(page, message: str) -> Dict[str, Any]:
    title = await page.title()
    html_content = await page.content()
    content, links, nav_links = extract_clean_content(
        html_content, base_url=page.url, max_text_length=8000
    )
    panel = await detect_form_panel_state(page)
    result: Dict[str, Any] = {
        "success": True,
        "url": page.url,
        "title": title,
        "message": message,
        "content": content,
        "links": links,
        "navigation_links": nav_links,
        "form_panel_state": panel,
    }
    if panel.get("panel_open"):
        result["instruction"] = (
            "Edit panel is open. Next: fill_form_field on empty fields, then "
            f"click_element(intent=\"{panel.get('recommended_intent', 'Save')}\"). "
            "Do NOT call observe_page or click the edit trigger again."
        )
    return result


async def _try_submit_button_click(page, intent: str) -> Optional[Dict[str, Any]]:
    """Fast DOM click for Save/Submit buttons — no LLM or vision needed."""
    if not _is_submit_intent(intent):
        return None

    # Playwright role match (most reliable for visible button text)
    for label in ("Save changes", "Save", "Submit", "Apply", "Confirm", "Update"):
        try:
            loc = page.get_by_role("button", name=label, exact=False)
            if await loc.count() > 0:
                await loc.first.click(timeout=4000)
                await page.wait_for_timeout(2000)
                print(f"✅ [SUBMIT CLICK] Clicked button '{label}' for intent '{intent}'")
                return await _build_click_success_response(
                    page, f"Clicked submit button '{label}'."
                )
        except Exception:
            continue

    # DOM fallback with data-agent-submit tagging
    btn_text = await page.evaluate("""() => {
        const isVisible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0
                && style.visibility !== 'hidden' && style.display !== 'none';
        };
        for (const el of document.querySelectorAll('button, input[type=submit]')) {
            if (!isVisible(el)) continue;
            const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
            if (!text) continue;
            const lower = text.toLowerCase();
            if (lower.includes('save') || lower.includes('submit') || lower.includes('apply')) {
                el.setAttribute('data-agent-submit', 'true');
                return text;
            }
        }
        return null;
    }""")
    if btn_text:
        await page.click('[data-agent-submit="true"]', timeout=4000)
        await page.evaluate("""() => {
            const el = document.querySelector('[data-agent-submit="true"]');
            if (el) el.removeAttribute('data-agent-submit');
        }""")
        await page.wait_for_timeout(2000)
        print(f"✅ [SUBMIT CLICK] Clicked '{btn_text}' via DOM for intent '{intent}'")
        return await _build_click_success_response(page, f"Clicked submit button '{btn_text}'.")

    return None


async def _try_aria_label_click(page, intent: str) -> Optional[Dict[str, Any]]:
    """Match icon-only controls via aria-label / visible text keyword scoring."""
    keywords = _extract_click_keywords(intent)
    if not keywords:
        return None

    elements = await page.evaluate(SCAN_INTERACTIVE_ELEMENTS_JS)
    best_label: Optional[str] = None
    best_score = 0

    for el in elements:
        label = (el.get("ariaLabel") or el.get("text") or "").strip()
        if not label:
            continue
        label_lower = label.lower()
        score = sum(1 for kw in keywords if kw in label_lower)
        # For edit intents, require the label to actually mention editing
        if _is_edit_trigger_intent(intent) and "edit" not in label_lower:
            continue
        if score > best_score:
            best_score = score
            best_label = label

    if not best_label or best_score < 2:
        return None

    print(
        f"🎯 [ARIA-LABEL MATCH] intent='{intent}' → label='{best_label}' "
        f"(score={best_score}, keywords={keywords})"
    )

    try:
        for locator_fn in [
            lambda: page.get_by_label(best_label, exact=True),
            lambda: page.locator(f'[aria-label="{best_label}"]'),
            lambda: page.locator("summary", has_text=best_label),
            lambda: page.get_by_role("button", name=best_label, exact=False),
        ]:
            try:
                loc = locator_fn()
                if await loc.count() > 0:
                    await loc.first.click(timeout=4000)
                    await page.wait_for_timeout(2000)
                    return await _build_click_success_response(
                        page, f"Clicked label match '{best_label}' for intent '{intent}'."
                    )
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"Label click failed for '{best_label}': {e}")
    return None


async def _vision_click_fallback(page, intent: str) -> Optional[Dict[str, Any]]:
    """Use vision model to find pixel coordinates and click when DOM matching fails."""
    try:
        viewport = page.viewport_size or {"width": 1920, "height": 1080}
        safe = re.sub(r"[^\w\-]", "_", intent)[:40]
        base64_img, screenshot_path = await capture_screenshot_for_vision(
            page, context_label=f"vision_click_{safe}"
        )
        print(f"📸 [VISION FALLBACK] Screenshot saved before coordinate lookup: {screenshot_path}")
        coords = await vision_click_coordinates(
            base64_img,
            intent,
            viewport_width=viewport["width"],
            viewport_height=viewport["height"],
            screenshot_path=screenshot_path,
        )
        if not coords:
            return None
        x, y = coords
        print(f"🖱️  [VISION CLICK] Clicking at ({x}, {y}) for intent '{intent}'")
        await page.mouse.click(x, y)
        await page.wait_for_timeout(2000)
        await page.wait_for_load_state("domcontentloaded", timeout=4000)
        return {"success": True, "clicked_via": "vision", "coordinates": [x, y]}
    except Exception as e:
        logger.error(f"Vision click fallback failed: {e}")
        return None


async def _click_handle_failure(page, intent: str, error_msg: str) -> Dict[str, Any]:
    try:
        print(f"⚠️  [CLICK FAILED] '{intent}' — calling vision API for guidance...")
        safe = re.sub(r"[^\w\-]", "_", intent)[:40]
        base64_img, screenshot_path = await capture_screenshot_for_vision(
            page, context_label=f"click_failed_{safe}"
        )
        vision_guidance = await analyze_page_screenshot(
            base64_img,
            f"Click element: {intent}",
            screenshot_path=screenshot_path,
        )
        return {
            "success": False,
            "error": error_msg,
            "vision_guidance": vision_guidance,
            "screenshot_path": screenshot_path,
        }
    except Exception as ve:
        logger.error(f"Vision analysis fallback failed: {ve}")
        return {
            "success": False,
            "error": error_msg,
            "vision_guidance": f"Vision guidance failed: {str(ve)}",
        }


async def click_element(intent: str) -> Dict[str, Any]:
    """
    Click a button or interactive element on the current page by describing
    what you want to click. Works on buttons without href links and icon-only
    controls identified by aria-label.
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {
            "success": False,
            "error": "No active browser session. Call browse_web first."
        }

    await page.wait_for_timeout(1000)

    panel = await detect_form_panel_state(page)

    # Save/submit — try DOM first, never re-open edit panel
    if _is_submit_intent(intent):
        submit_result = await _try_submit_button_click(page, intent)
        if submit_result:
            return submit_result

    # Do NOT re-click edit trigger when panel is already open (toggles it closed)
    if _is_edit_trigger_intent(intent) and panel.get("panel_open"):
        print("⚠️  [SKIP RE-EDIT] Panel already open — refusing to click edit trigger again")
        return {
            "success": True,
            "skipped_redundant_click": True,
            "message": (
                "Edit panel is already open. Do NOT click the edit trigger again — "
                "it will close the panel. Call fill_form_field then click_element on Save."
            ),
            "form_panel_state": panel,
            "instruction": (
                f"Call fill_form_field(field_description=\"{panel.get('recommended_intent', 'description')}\", "
                f"value=<text>) then click_element(intent=\"{panel.get('submit_buttons', ['Save changes'])[0]}\")."
            ),
        }

    if _is_edit_trigger_intent(intent):
        scroll_y = await page.evaluate("() => window.scrollY")
        if scroll_y > 100:
            await scroll_to_top(page)

    # Fast path: aria-label keyword match for icon-only edit/metadata buttons
    if _is_edit_trigger_intent(intent):
        aria_result = await _try_aria_label_click(page, intent)
        if aria_result:
            return aria_result

    client = get_openai_client()
    elements = await page.evaluate(SCAN_INTERACTIVE_ELEMENTS_JS)

    if not elements:
        return await _click_handle_failure(page, intent, "No clickable elements found on page.")

    formatted = "\n".join(
        f"- Tag: {e['tag']} | Text: '{e['text']}' | aria-label: '{e.get('ariaLabel', '')}' | Selector: {e['selector']}"
        for e in elements
    )

    prompt = (
        f"User wants to click: \"{intent}\"\n\n"
        f"Available clickable elements (Text may come from aria-label for icon-only buttons):\n{formatted}\n\n"
        "Return ONLY the text or aria-label of the best matching element, exactly as shown. "
        "Prefer aria-label values for icon-only buttons. Return 'None' if nothing matches."
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
        return await _click_handle_failure(page, intent, f"LLM selector pick failed: {e}")

    if target_text.lower() == "none":
        if _is_submit_intent(intent):
            submit_result = await _try_submit_button_click(page, intent)
            if submit_result:
                return submit_result
        aria_result = await _try_aria_label_click(page, intent)
        if aria_result:
            return aria_result
        vision_result = await _vision_click_fallback(page, intent)
        if vision_result:
            return await _build_click_success_response(
                page,
                f"Clicked via vision fallback at {vision_result['coordinates']} for intent '{intent}'.",
            )
        return await _click_handle_failure(page, intent, f"No element found matching: '{intent}'")

    try:
        match_result = await page.evaluate(MATCH_ELEMENT_BY_TEXT_JS, target_text)
        tagged = match_result.get("found", False)
        if match_result.get("matchedText"):
            target_text = match_result["matchedText"]

        if tagged:
            is_toggle = await page.evaluate("""() => {
                const el = document.querySelector('[data-agent-click="true"]');
                if (!el) return false;
                if (el.tagName === 'A' && el.getAttribute('href') && !el.getAttribute('href').startsWith('#')) {
                    return false;
                }
                if (el.tagName === 'SUMMARY') return true;
                if (el.getAttribute('aria-haspopup') && el.getAttribute('aria-haspopup') !== 'false') return true;
                if (el.getAttribute('aria-expanded') !== null) return true;
                const classes = (el.className || '').toLowerCase();
                if (classes.includes('dropdown') || classes.includes('menu-toggle')) return true;
                return false;
            }""")

            if is_toggle:
                await page.evaluate("""() => {
                    const el = document.querySelector('[data-agent-click="true"]');
                    if (el) {
                        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        if (typeof el.focus === 'function') el.focus();
                    }
                }""")
            else:
                try:
                    await page.click('[data-agent-click="true"]', timeout=2000)
                except Exception:
                    await page.evaluate("""() => {
                        const el = document.querySelector('[data-agent-click="true"]');
                        if (el) {
                            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                            if (typeof el.focus === 'function') el.focus();
                        }
                    }""")

            await page.evaluate("""() => {
                const el = document.querySelector('[data-agent-click="true"]');
                if (el) el.removeAttribute('data-agent-click');
            }""")
        else:
            # Playwright role/label fallbacks before vision
            clicked = False
            for locator_fn in [
                lambda: page.get_by_label(target_text, exact=False),
                lambda: page.get_by_role("button", name=target_text, exact=False),
                lambda: page.locator(f'[aria-label="{target_text}"]'),
                lambda: page.get_by_text(target_text, exact=False),
            ]:
                try:
                    loc = locator_fn()
                    if await loc.count() > 0:
                        await loc.first.click(timeout=4000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                vision_result = await _vision_click_fallback(page, intent)
                if not vision_result:
                    raise RuntimeError(f"No element found for '{target_text}'")

        await page.wait_for_timeout(2000)
        await page.wait_for_load_state("domcontentloaded", timeout=4000)

        if not (_is_edit_trigger_intent(intent) or _is_submit_intent(intent)):
            await scroll_to_bottom(page)

        return await _build_click_success_response(
            page, f"Clicked '{target_text}' successfully."
        )
    except Exception as e:
        logger.error(f"Click failed for '{target_text}': {e}")
        if _is_submit_intent(intent):
            submit_result = await _try_submit_button_click(page, intent)
            if submit_result:
                return submit_result
        aria_result = await _try_aria_label_click(page, intent)
        if aria_result:
            return aria_result
        vision_result = await _vision_click_fallback(page, intent)
        if vision_result:
            return await _build_click_success_response(
                page,
                f"Recovered via vision click at {vision_result['coordinates']} for '{intent}'.",
            )
        return await _click_handle_failure(page, intent, f"Click failed: {str(e)}")
