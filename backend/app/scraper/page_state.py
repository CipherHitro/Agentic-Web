"""Detect inline edit panels / dialogs so the agent can fill+save without re-observing."""

from typing import Any, Dict, Optional

from playwright.async_api import Page

DETECT_FORM_PANEL_JS = """() => {
    const isVisible = (el) => {
        if (!el || !el.isConnected) return false;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const getLabel = (el) => {
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label').trim();
        if (el.id) {
            const lbl = document.querySelector(`label[for="${el.id}"]`);
            if (lbl) return lbl.textContent.trim();
        }
        const labelledby = el.getAttribute('aria-labelledby');
        if (labelledby) {
            const parts = labelledby.split(' ').map(id => document.getElementById(id))
                .filter(Boolean).map(n => n.textContent.trim()).filter(Boolean);
            if (parts.length) return parts.join(' ');
        }
        let cur = el.parentElement;
        for (let i = 0; i < 4 && cur; i++) {
            const h = cur.querySelector('label, legend, [role="heading"]');
            if (h && h.textContent.trim()) return h.textContent.trim();
            cur = cur.parentElement;
        }
        return el.placeholder || el.name || el.id || '';
    };

    const fields = [];
    document.querySelectorAll(
        'textarea, input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio])'
    ).forEach((el, idx) => {
        if (!isVisible(el)) return;
        el.setAttribute('data-agent-field', String(idx));
        fields.push({
            index: idx,
            tag: el.tagName.toLowerCase(),
            label: getLabel(el).slice(0, 120),
            type: el.type || 'text',
            hasValue: !!(el.value || '').trim(),
            isTextarea: el.tagName === 'TEXTAREA',
        });
    });

    const submitButtons = [];
    document.querySelectorAll('button, input[type=submit], [role="button"]').forEach((el, idx) => {
        if (!isVisible(el)) return;
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
        if (!text) return;
        const lower = text.toLowerCase();
        if (lower.includes('save') || lower.includes('submit') || lower.includes('apply')
            || lower.includes('confirm') || lower === 'done' || lower.includes('update')) {
            el.setAttribute('data-agent-submit', String(idx));
            submitButtons.push(text);
        }
    });

    const panelOpen = fields.length > 0 && submitButtons.length > 0;
    const emptyFields = fields.filter(f => !f.hasValue);
    const textareas = fields.filter(f => f.isTextarea);

    let recommendedNext = null;
    let recommendedIntent = null;
    if (panelOpen) {
        const emptyFields = fields.filter(f => !f.hasValue);
        const emptyTextareas = textareas.filter(f => !f.hasValue);
        const descField = emptyFields.find(f =>
            f.isTextarea || /description|about|bio|summary/i.test(f.label)
        );
        if (emptyFields.length > 0) {
            recommendedNext = 'fill_form_field';
            const target = descField || emptyTextareas[0] || emptyFields[0];
            recommendedIntent = target.isTextarea
                ? 'description'
                : (target.label || 'field');
        } else {
            recommendedNext = 'click_element';
            recommendedIntent = submitButtons[0];
        }
    }

    return {
        panel_open: panelOpen,
        fields,
        submit_buttons: submitButtons,
        empty_field_count: emptyFields.length,
        recommended_next: recommendedNext,
        recommended_intent: recommendedIntent,
    };
}"""


async def detect_form_panel_state(page: Optional[Page]) -> Dict[str, Any]:
    if not page or page.is_closed():
        return {"panel_open": False, "fields": [], "submit_buttons": []}
    try:
        return await page.evaluate(DETECT_FORM_PANEL_JS)
    except Exception:
        return {"panel_open": False, "fields": [], "submit_buttons": []}
