import logging
from typing import Any, Dict
from app.scraper.browser import browser_manager
from app.scraper.page_handler import scroll_to_bottom
from app.tools.extraction_tools import extract_clean_content
from app.services.llm_service import get_openai_client
from app.config import settings

from app.scraper.screenshot import capture_screenshot_base64
from app.services.vision_service import analyze_page_screenshot

logger = logging.getLogger(__name__)


async def _click_handle_failure(page, intent: str, error_msg: str) -> Dict[str, Any]:
    try:
        logger.info(f"Click failed for '{intent}'. Fetching vision guidance...")
        base64_img = await capture_screenshot_base64(page)
        vision_guidance = await analyze_page_screenshot(base64_img, f"Click element: {intent}")
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

    # Wait for layout/animations to settle before scanning elements
    await page.wait_for_timeout(1000)

    html = await page.content()
    base_url = page.url

    # Step 1: Ask LLM to pick the best CSS selector for this intent
    client = get_openai_client()

    # Build a list of interactive elements from the page
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
            return text;
        };

        const seen = new Set();
        const results = [];
        
        const selectors = [
            'button', '[role="button"]', 'input[type="submit"]',
            'input[type="button"]', 'a[href]', '[onclick]',
            'summary',          // <details><summary> dropdowns (GitHub uses this!)
            '[tabindex="0"]',   // keyboard-focusable custom elements
            'label[for]'        // clickable labels
        ];
        
        document.querySelectorAll(selectors.join(',')).forEach(el => {
            // Only visible elements
            if (el.offsetParent === null && el.tagName !== 'SUMMARY') return;
            
            const text = getElementText(el);
            const key = text + el.tagName + el.className;
            if (seen.has(key) || !text) return;
            seen.add(key);
            
            results.push({
                text: text.slice(0, 100),
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                id: el.id || '',
                classes: el.className.slice(0, 60),
                selector: el.id ? `#${el.id}` : (el.tagName.toLowerCase() + (el.className ? `.${el.className.split(' ')[0]}` : ''))
            });
        });
        return results.slice(0, 60);
    }""")

    if not elements:
        return await _click_handle_failure(page, intent, "No clickable elements found on page.")

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
        return await _click_handle_failure(page, intent, f"LLM selector pick failed: {e}")

    if target_text.lower() == "none":
        return await _click_handle_failure(page, intent, f"No element found matching: '{intent}'")

    # Step 2: Click it using text matching (most reliable cross-site approach)
    try:
        # Tag the target element in the DOM using the same getElementText scanner rules
        tagged = await page.evaluate("""(txt) => {
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
                return text;
            };

            const selectors = [
                'button', '[role="button"]', 'input[type="submit"]',
                'input[type="button"]', 'a[href]', '[onclick]',
                'summary', '[tabindex="0"]', 'label[for]'
            ];

            const candidates = Array.from(document.querySelectorAll(selectors.join(',')))
                .filter(el => el.offsetParent !== null || el.tagName === 'SUMMARY');

            // Exact match priority
            let match = candidates.find(el => getElementText(el).trim().toLowerCase() === txt.trim().toLowerCase());
            if (!match) {
                // Substring fallback
                match = candidates.find(el => getElementText(el).toLowerCase().includes(txt.toLowerCase()));
            }

            if (match) {
                match.setAttribute('data-agent-click', 'true');
                return true;
            }
            return false;
        }""", target_text)

        if tagged:
            tagged_html = await page.evaluate("""() => {
                const el = document.querySelector('[data-agent-click="true"]');
                return el ? el.outerHTML : 'None';
            }""")
            logger.debug(f"Tagged element outer HTML: {tagged_html}")

            # Check if this element is a menu/dropdown/popup toggle
            is_toggle = await page.evaluate("""() => {
                const el = document.querySelector('[data-agent-click="true"]');
                if (!el) return false;
                if (el.tagName === 'SUMMARY') return true;
                if (el.getAttribute('aria-haspopup') && el.getAttribute('aria-haspopup') !== 'false') return true;
                if (el.getAttribute('aria-expanded') !== null) return true;
                
                const classes = (el.className || '').toLowerCase();
                const id = (el.id || '').toLowerCase();
                if (classes.includes('menu') || classes.includes('dropdown') || classes.includes('toggle') || classes.includes('avatar')) return true;
                if (id.includes('menu') || id.includes('dropdown') || id.includes('toggle') || id.includes('avatar')) return true;
                return false;
            }""")

            # Also check text
            is_text_toggle = any(x in target_text.lower() for x in ["settings", "profile", "avatar", "menu", "open", "cipherhitro", "rohit"])

            if is_toggle or is_text_toggle:
                logger.info(f"Target '{target_text}' identified as a menu/dropdown toggle. Executing DOM click dispatch only to avoid double-toggling.")
                await page.evaluate("""() => {
                    const el = document.querySelector('[data-agent-click="true"]');
                    if (el) {
                        el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        if (typeof el.focus === 'function') el.focus();
                    }
                }""")
            else:
                # Try Playwright native pointer click first for normal buttons/links
                try:
                    await page.click('[data-agent-click="true"]', timeout=2000)
                except Exception as native_err:
                    logger.warning(f"Native click failed for normal element: {native_err}. Falling back to DOM click dispatch.")
                    await page.evaluate("""() => {
                        const el = document.querySelector('[data-agent-click="true"]');
                        if (el) {
                            el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                            if (typeof el.focus === 'function') el.focus();
                        }
                    }""")
            
            # Clean up attribute
            await page.evaluate("""() => {
                const el = document.querySelector('[data-agent-click="true"]');
                if (el) el.removeAttribute('data-agent-click');
            }""")
        else:
            # Fallback to standard selectors if DOM tagging failed
            summary_locator = page.locator("summary", has_text=target_text)
            if await summary_locator.count() > 0:
                await summary_locator.first.click()
            else:
                locator = page.get_by_role("button", name=target_text, exact=False)
                if await locator.count() == 0:
                    locator = page.get_by_text(target_text, exact=False).first
                await locator.click(timeout=4000)

        # Wait for potential animation or navigation
        await page.wait_for_timeout(2000)
        await page.wait_for_load_state("domcontentloaded", timeout=4000)

        # Skip scrolling for dropdown/settings clicks
        is_dropdown = any(x in target_text.lower() for x in ["settings", "profile", "avatar", "menu", "open", "cipherhitro", "rohit"])
        if not is_dropdown:
            await scroll_to_bottom(page)

        # Check for validation/required field errors on the page after clicking
        validation_errors = await page.evaluate("""
            () => {
                const errors = [];
                document.querySelectorAll(':invalid').forEach(el => {
                    if (el.validationMessage) {
                        errors.push(el.validationMessage);
                    }
                });
                document.querySelectorAll('[aria-invalid="true"]').forEach(el => {
                    let errorText = "Invalid field";
                    const parent = el.closest('[role="listitem"], .Qr7Oae, .M7eCdd');
                    if (parent) {
                        const heading = parent.querySelector('[role="heading"], label, legend');
                        if (heading && heading.textContent.trim()) {
                            errorText = `Field "${heading.textContent.trim()}" is invalid/required`;
                        } else {
                            errorText = `Field "${parent.textContent.trim().slice(0, 50)}..." is invalid/required`;
                        }
                    }
                    if (!errors.includes(errorText)) {
                        errors.push(errorText);
                    }
                });
                const errorSelectors = ['[role="alert"]', '.errorMessage', '.error-message', '.validation-error', '.R9Z5ct'];
                for (const sel of errorSelectors) {
                    document.querySelectorAll(sel).forEach(el => {
                        const text = el.textContent.trim();
                        if (text && !errors.includes(text)) {
                            errors.push(text);
                        }
                    });
                }
                return errors;
            }
        """)

        title = await page.title()
        html_content = await page.content()
        content, links, nav_links = extract_clean_content(
            html_content, base_url=page.url, max_text_length=8000
        )

        message = f"Clicked '{target_text}' successfully."
        if validation_errors:
            err_str = "; ".join(validation_errors)
            message += f" WARNING: Form validation/required field error(s) detected on page: '{err_str}'. The submission or action may have failed. Please check the fields and make sure they are filled correctly."

        return {
            "success": True,
            "url": page.url,
            "title": title,
            "message": message,
            "content": content,
            "links": links,
            "navigation_links": nav_links,
        }
    except Exception as e:
        logger.error(f"Click failed for '{target_text}': {e}")
        return await _click_handle_failure(page, intent, f"Click failed: {str(e)}")
