import asyncio
import logging
from typing import Dict, Any

from app.config import settings

logger = logging.getLogger(__name__)

# Shared global state for human interaction (development only)
human_request: Dict[str, Any] = {"waiting": False, "prompt": None}
_human_response_queue: asyncio.Queue = asyncio.Queue()

PRODUCTION_HUMAN_BLOCK_MESSAGE = (
    "Human involvement is disabled in production. The user cannot interact with the "
    "browser session on the server. Tasks requiring login, sign-in, MFA, CAPTCHA, or "
    "manual authentication cannot be completed here. Call finish_task and explain that "
    "this step requires interactive browser access (e.g. signing in), which is not "
    "available in production deployment."
)


async def request_human_input(prompt: str) -> Dict[str, Any]:
    """
    Pause the agent and wait for human input via the frontend (development only).
    In production, returns an immediate failure so the agent can finish_task honestly.
    """
    if not settings.human_involvement_enabled:
        logger.warning(
            "request_human_input blocked in production mode. prompt=%s", prompt[:120]
        )
        print(
            "🚫 [PRODUCTION] Human handoff blocked — user cannot interact with browser"
        )
        return {
            "success": False,
            "error": PRODUCTION_HUMAN_BLOCK_MESSAGE,
            "production_limitation": True,
            "requested_prompt": prompt,
        }

    logger.info(f"Pausing agent: human input requested: {prompt}")
    print(f"🙋 [HUMAN HANDOFF] Waiting for user: {prompt[:200]}")
    human_request["waiting"] = True
    human_request["prompt"] = prompt

    try:
        while not _human_response_queue.empty():
            _human_response_queue.get_nowait()

        response = await asyncio.wait_for(_human_response_queue.get(), timeout=300)
        return {"success": True, "human_response": response.get("answer", "")}
    except asyncio.TimeoutError:
        logger.warning("Human input timed out.")
        return {"success": False, "error": "Human input timed out after 5 minutes."}
    finally:
        human_request["waiting"] = False
        human_request["prompt"] = None
