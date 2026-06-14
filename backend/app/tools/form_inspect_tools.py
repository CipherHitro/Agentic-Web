import logging
from typing import Any, Dict, List
from app.scraper.browser import browser_manager

logger = logging.getLogger(__name__)

_FORM_SCAN_JS = """
() => {
    const result = [];

    // --- Strategy 1: ARIA-based (Google Forms style) ---
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
        const textInput = item.querySelector('input[type="text"], input[type="email"], input[type="number"], textarea');
        const radios = item.querySelectorAll('[role="radio"]');
        const checkboxes = item.querySelectorAll('[role="checkbox"]');
        const listbox = item.querySelector('[role="listbox"], select');

        if (textInput) {
            result.push({ question, type: 'text' });
        } else if (radios.length > 0) {
            result.push({
                question,
                type: 'radio_or_rating',
                options: Array.from(radios).map(r => (r.getAttribute('aria-label') || r.textContent.trim()))
            });
        } else if (checkboxes.length > 0) {
            result.push({
                question,
                type: 'checkbox',
                options: Array.from(checkboxes).map(c => (c.getAttribute('aria-label') || c.textContent.trim()))
            });
        } else if (listbox) {
            const options = Array.from(listbox.querySelectorAll('[role="option"], option'))
                .map(o => o.getAttribute('data-value') || o.textContent.trim())
                .filter(Boolean);
            result.push({
                question,
                type: 'radio_or_rating',
                options: options
            });
        }
    }
    if (result.length > 0) return result;

    // --- Strategy 2: Native HTML form controls (fallback for non-Google forms) ---
    const groups = {};
    document.querySelectorAll('input[type="radio"], input[type="checkbox"]').forEach(el => {
        const name = el.name || ('unnamed_' + (el.id || Math.random()));
        if (!groups[name]) groups[name] = { type: el.type, options: [], el };
        let label = '';
        if (el.id) {
            const lbl = document.querySelector(`label[for="${el.id}"]`);
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parentLabel = el.closest('label');
            if (parentLabel) label = parentLabel.textContent.trim();
        }
        groups[name].options.push(label || el.value || '');
    });
    for (const g of Object.values(groups)) {
        let question = g.el.name || '';
        const fieldset = g.el.closest('fieldset');
        if (fieldset) {
            const legend = fieldset.querySelector('legend');
            if (legend) question = legend.textContent.trim();
        }
        result.push({
            question,
            type: g.type === 'radio' ? 'radio_or_rating' : 'checkbox',
            options: g.options
        });
    }

    // Standard HTML selects
    document.querySelectorAll('select').forEach(el => {
        let question = el.name || el.id || '';
        const label = document.querySelector(`label[for="${el.id}"]`);
        if (label) question = label.textContent.trim();
        const options = Array.from(el.querySelectorAll('option'))
            .map(o => o.textContent.trim())
            .filter(Boolean);
        result.push({
            question,
            type: 'radio_or_rating',
            options: options
        });
    });

    document.querySelectorAll('input[type="text"], input[type="email"], input[type="number"], textarea').forEach(el => {
        let question = el.placeholder || el.name || el.id || '';
        if (el.id) {
            const lbl = document.querySelector(`label[for="${el.id}"]`);
            if (lbl) question = lbl.textContent.trim();
        }
        result.push({ question, type: 'text' });
    });

    return result;
}
"""


async def read_form_fields() -> Dict[str, Any]:
    """
    Scan the current page and return every form question along with its type
    (text / radio_or_rating / checkbox) and available options.
    ALWAYS call this BEFORE filling any field on a form.
    """
    page = browser_manager.current_page
    if not page or page.is_closed():
        return {"success": False, "error": "No active browser session. Call browse_web first."}

    try:
        fields: List[Dict[str, Any]] = []
        for frame in [page] + list(page.frames):
            try:
                frame_fields = await frame.evaluate(_FORM_SCAN_JS)
                if frame_fields:
                    fields.extend(frame_fields)
            except Exception:
                continue

        if not fields:
            return {"success": False, "error": "No form fields detected on this page."}

        return {"success": True, "fields": fields}
    except Exception as e:
        logger.error(f"read_form_fields failed: {e}")
        return {"success": False, "error": f"read_form_fields failed: {str(e)}"}
