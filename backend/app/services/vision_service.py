import logging
import re
from typing import Optional, Tuple

from openai import AsyncOpenAI
from app.config import settings

logger = logging.getLogger(__name__)


def _get_vision_client_and_model() -> Tuple[AsyncOpenAI, str]:
    nvidia_key = settings.nemotron_nvidia
    if nvidia_key:
        return (
            AsyncOpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=nvidia_key,
            ),
            "meta/llama-3.2-11b-vision-instruct",
        )
    if not settings.openrouter_api_key:
        raise ValueError("No vision API keys configured in .env.")
    return (
        AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
        ),
        "google/gemini-2.5-flash",
    )


def _log_vision_exchange(
    kind: str,
    goal: str,
    prompt: str,
    response: str,
    screenshot_path: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """Print vision request/response to terminal for debugging."""
    print(f"\n{'=' * 54}")
    print(f"👁️  [VISION {kind}]" + (f"  model={model}" if model else ""))
    print(f"{'=' * 54}")
    print(f"GOAL: {goal}")
    if screenshot_path:
        print(f"SCREENSHOT SAVED: {screenshot_path}")
    print(f"{'-' * 54}")
    print("PROMPT SENT TO VISION API:")
    print(prompt)
    print(f"{'-' * 54}")
    print("VISION API RESPONSE:")
    print(response)
    print(f"{'=' * 54}\n")


async def analyze_page_screenshot(
    base64_image: str,
    goal: str,
    screenshot_path: Optional[str] = None,
) -> str:
    """
    Sends a base64 encoded screenshot to a vision model for element-location guidance.
    """
    try:
        client, model_name = _get_vision_client_and_model()

        prompt = f"""You are a vision-guided web assistant. The autonomous browser agent is trying to accomplish this goal: "{goal}"
Observe the screenshot of the current web page. Provide:
1. Where the relevant buttons, input fields, settings cogs, edit controls, or navigation links are located (especially check the right sidebar, headers, and look for custom icons/cogs/pencils that might lack visible text but have aria-labels).
2. What exact element text, aria-label, or selector should be clicked next to achieve the goal.
3. Precise, step-by-step instructions (e.g. "Click the settings gear icon in the 'About' section on the right", "Click the 'Sign out' button").
Keep your feedback concise, actionable, and specific for the text-based web agent."""

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                    },
                ],
            }
        ]

        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=400,
            temperature=0.0,
        )
        result = response.choices[0].message.content.strip()
        _log_vision_exchange(
            "ANALYZE", goal, prompt, result, screenshot_path, model_name
        )
        return result
    except Exception as e:
        err = f"Vision guidance failed: {str(e)}"
        logger.error(err)
        _log_vision_exchange("ANALYZE — ERROR", goal, goal, err, screenshot_path)
        return err


async def vision_click_coordinates(
    base64_image: str,
    intent: str,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    screenshot_path: Optional[str] = None,
) -> Optional[Tuple[float, float]]:
    """Ask the vision model for pixel coordinates of a clickable element."""
    try:
        client, model_name = _get_vision_client_and_model()
        prompt = (
            f"This is a screenshot ({viewport_width}x{viewport_height}px). "
            f"Find the clickable element matching this intent: \"{intent}\". "
            "Look for pencil/edit icons, gear icons, icon-only buttons with aria-labels, "
            "'Edit' controls in sidebars/headers, and metadata/settings buttons even if "
            "they have no visible text. "
            "Return ONLY pixel coordinates as 'x,y' for the CENTER of the element "
            "(e.g. '420,310'). Return 'None' if you cannot find it."
        )
        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{base64_image}"},
                        },
                    ],
                }
            ],
            max_tokens=80,
            temperature=0.0,
        )
        coords_str = response.choices[0].message.content.strip().strip("\"'")
        _log_vision_exchange(
            "CLICK-COORDS",
            intent,
            prompt,
            coords_str,
            screenshot_path,
            model_name,
        )
        if coords_str.lower() == "none" or "," not in coords_str:
            return None
        match = re.search(r"([\d.]+)\s*,\s*([\d.]+)", coords_str)
        if not match:
            return None
        return float(match.group(1)), float(match.group(2))
    except Exception as e:
        err = f"Vision click coordinate lookup failed: {str(e)}"
        logger.error(err)
        _log_vision_exchange("CLICK-COORDS — ERROR", intent, intent, err, screenshot_path)
        return None
