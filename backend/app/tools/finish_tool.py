import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def finish_task(answer: str, sources: list[str] = None) -> Dict[str, Any]:
    """
    Submit the final answer and terminate the task.
    Use this ONLY when you have gathered all necessary information and are ready
    to provide the final complete answer to the user.
    """
    logger.info(f"finish_task tool called with final answer. Sources: {sources}")
    return {
        "success": True,
        "answer": answer,
        "sources": sources or []
    }
