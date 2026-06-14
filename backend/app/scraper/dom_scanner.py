"""Shared DOM scanning JS for interactive element discovery."""

SCAN_INTERACTIVE_ELEMENTS_JS = """() => {
    const isVisible = (el) => {
        if (!el || !el.isConnected) return false;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const getElementText = (el) => {
        let text = (
            el.getAttribute('aria-label') ||
            el.innerText || el.value ||
            el.getAttribute('title') || el.getAttribute('alt') ||
            el.getAttribute('placeholder') || el.getAttribute('data-login') || ''
        ).trim();
        if (!text) {
            const childImg = el.querySelector('img, svg');
            if (childImg) {
                text = (
                    childImg.getAttribute('aria-label') ||
                    childImg.getAttribute('alt') ||
                    childImg.getAttribute('title') || ''
                ).trim();
            }
        }
        if (!text) {
            for (const child of el.querySelectorAll('*')) {
                text = (
                    child.getAttribute('aria-label') ||
                    child.innerText || child.getAttribute('alt') || ''
                ).trim();
                if (text) break;
            }
        }
        return text;
    };

    const isClickable = (el) => {
        const tag = el.tagName;
        if (['BUTTON', 'A', 'SUMMARY', 'INPUT', 'LABEL'].includes(tag)) return true;
        const role = el.getAttribute('role');
        if (role === 'button' || role === 'link' || role === 'menuitem') return true;
        if (el.hasAttribute('onclick') || el.hasAttribute('tabindex')) return true;
        return false;
    };

    const seen = new Set();
    const results = [];

    const selectors = [
        'button', '[role="button"]', 'input[type="submit"]', 'input[type="button"]',
        'a[href]', '[onclick]', 'summary', '[tabindex="0"]', 'label[for]',
        '[aria-label]'
    ];

    document.querySelectorAll(selectors.join(',')).forEach(el => {
        if (!isVisible(el) && el.tagName !== 'SUMMARY') return;
        if (!isClickable(el) && !el.getAttribute('aria-label')) return;

        const text = getElementText(el);
        const ariaLabel = (el.getAttribute('aria-label') || '').trim();
        const displayText = text || ariaLabel;
        if (!displayText) return;

        const key = displayText + el.tagName + (el.className || '');
        if (seen.has(key)) return;
        seen.add(key);

        results.push({
            text: displayText.slice(0, 120),
            tag: el.tagName.toLowerCase(),
            type: el.type || '',
            ariaLabel: ariaLabel,
            title: el.getAttribute('title') || '',
            id: el.id || '',
            classes: (el.className || '').toString().slice(0, 60),
            selector: el.id
                ? `#${el.id}`
                : (ariaLabel
                    ? `[aria-label="${ariaLabel.replace(/"/g, '\\\\"')}"]`
                    : el.tagName.toLowerCase())
        });
    });

    return results.slice(0, 80);
}"""

MATCH_ELEMENT_BY_TEXT_JS = """(txt) => {
    const isVisible = (el) => {
        if (!el || !el.isConnected) return false;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };

    const getElementText = (el) => {
        let text = (
            el.getAttribute('aria-label') ||
            el.innerText || el.value ||
            el.getAttribute('title') || el.getAttribute('alt') ||
            el.getAttribute('placeholder') || ''
        ).trim();
        if (!text) {
            for (const child of el.querySelectorAll('*')) {
                text = (
                    child.getAttribute('aria-label') ||
                    child.innerText || child.getAttribute('alt') || ''
                ).trim();
                if (text) break;
            }
        }
        return text;
    };

    const selectors = [
        'button', '[role="button"]', 'input[type="submit"]', 'input[type="button"]',
        'a[href]', '[onclick]', 'summary', '[tabindex="0"]', 'label[for]', '[aria-label]'
    ];

    const candidates = Array.from(document.querySelectorAll(selectors.join(',')))
        .filter(el => isVisible(el) || el.tagName === 'SUMMARY');

    const target = txt.trim().toLowerCase();
    let match = candidates.find(el => getElementText(el).trim().toLowerCase() === target);
    if (!match) {
        match = candidates.find(el => getElementText(el).toLowerCase().includes(target));
    }
    if (!match && target) {
        match = candidates.find(el => {
            const label = getElementText(el).toLowerCase();
            return target.split(/\\s+/).filter(w => w.length > 2)
                .every(word => label.includes(word));
        });
    }

    if (match) {
        match.setAttribute('data-agent-click', 'true');
        return { found: true, matchedText: getElementText(match) };
    }
    return { found: false, matchedText: null };
}"""
