import logging
from openai import AsyncOpenAI
from app.config import settings

logger = logging.getLogger(__name__)

async def analyze_page_screenshot(base64_image: str, goal: str) -> str:
    """
    Sends a base64 encoded screenshot of the page to a vision model (NVIDIA NIM or OpenRouter fallback)
    to help locate elements or suggest next actions for the goal.
    """
    try:
        # Check if NVIDIA key is available in env (configured via settings.nemotron_nvidia)
        # Note: BaseSettings handles case insensitivity so NEMOTRON_NViDIA -> nemotron_nvidia
        nvidia_key = settings.nemotron_nvidia
        if nvidia_key:
            logger.info("Calling NVIDIA Multimodal API for vision guidance...")
            client = AsyncOpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=nvidia_key
            )
            # Use a standard, highly performant vision model hosted by NVIDIA
            model_name = "meta/llama-3.2-11b-vision-instruct"
        else:
            # Fall back to OpenRouter vision model
            logger.info("NVIDIA key not configured. Falling back to OpenRouter vision API...")
            if not settings.openrouter_api_key:
                return "Vision guidance unavailable: No API keys configured in .env."
            client = AsyncOpenAI(
                base_url=settings.openrouter_base_url,
                api_key=settings.openrouter_api_key
            )
            model_name = "google/gemini-2.5-flash"  # Flash supports vision and is very cheap

        prompt = f"""You are a vision-guided web assistant. The autonomous browser agent is trying to accomplish this goal: "{goal}"
Observe the screenshot of the current web page. Provide:
1. Where the relevant buttons, input fields, settings cogs, edit controls, or navigation links are located (especially check the right sidebar, headers, and look for custom icons/cogs/pencils that might lack text).
2. What exact element text, label, or selector should be clicked next to achieve the goal.
3. Precise, step-by-step instructions (e.g. "Click the settings gear icon in the 'About' section on the right", "Click the 'Sign out' button").
Keep your feedback concise, actionable, and specific for the text-based web agent."""

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]

        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=400,
            temperature=0.0
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Vision analysis failed: {e}")
        return f"Vision guidance failed: {str(e)}"
