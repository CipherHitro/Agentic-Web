import base64
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"


def _safe_label(label: str) -> str:
    return re.sub(r"[^\w\-]", "_", label)[:50].strip("_") or "capture"


async def capture_screenshot_base64(page: Page) -> str:
    screenshot_bytes = await page.screenshot(full_page=False)
    return base64.b64encode(screenshot_bytes).decode()


async def capture_screenshot_for_vision(
    page: Page,
    context_label: str = "vision",
    save: bool = True,
) -> tuple[str, Optional[str]]:
    """Capture viewport screenshot; optionally save to screenshots/ folder."""
    screenshot_bytes = await page.screenshot(full_page=False)
    b64 = base64.b64encode(screenshot_bytes).decode()
    filepath: Optional[str] = None

    if save:
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{timestamp}_{_safe_label(context_label)}.png"
        path = SCREENSHOTS_DIR / filename
        path.write_bytes(screenshot_bytes)
        filepath = str(path)

    return b64, filepath
