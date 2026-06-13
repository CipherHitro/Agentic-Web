import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List

from app.tools.browser_tools import browse_web
from app.tools.search_tools import search_web
from app.tools.extraction_tools import extract_data
from app.tools.navigation_tools import navigate_page
from app.tools.finish_tool import finish_task

logger = logging.getLogger(__name__)


ToolHandler = Callable[..., Awaitable[Dict[str, Any]]]


def get_tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "browse_web",
                "description": (
                    "Browse a website and extract its content. Use this when the user "
                    "asks about a specific website, wants to read a webpage, or needs "
                    "information from a URL."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The full URL to browse, including http:// or https://",
                        },
                        "extract_content": {
                            "type": "boolean",
                            "description": "Whether to extract the main text content",
                            "default": True,
                        },
                        "scroll_page": {
                            "type": "boolean",
                            "description": "Whether to scroll the page to load lazy content",
                            "default": True,
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the internet for current, real-time, recent, product, pricing, company,news, release, availability and factual information.Always use this tool when the user asks about current prices, current events, recent releases, product availability,or information that may have changed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to look up on the web"
                        },
                        "count": {
                            "type": "integer",
                            "description": "The number of search results to return",
                            "default": 5
                        }
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "extract_data",
                "description": (
                    "Extract specific data fields from a webpage's raw HTML or content. "
                    "Use this tool when you need structured fields like price, description, "
                    "date, or specific values from a webpage."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "The list of field names/descriptions to extract (e.g. ['price', 'product name'])",
                        },
                    },
                    "required": ["fields"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "navigate_page",
                "description": (
                    "Statefully navigate to a new page or sub-page matching a specific intent. "
                    "Use this tool to click links, open products, go to next pages, or click tabs. "
                    "IMPORTANT: For GitHub profiles, use intent like 'go to stars tab' or "
                    "'navigate to ?tab=stars'. Tab links often use URL query parameters."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": "The target navigation goal (e.g., 'click the first search result product', 'go to page 2', 'click on specifications tab')",
                        },
                    },
                    "required": ["intent"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": (
                    "Submit the final answer and terminate the task. "
                    "Use this ONLY when you have gathered all necessary information and are ready "
                    "to provide the final complete answer to the user. "
                    "You must NOT produce a final answer as plain text without calling this tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {
                            "type": "string",
                            "description": "The final complete and detailed answer to the user's query.",
                        },
                        "sources": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": "The list of absolute source URLs (e.g. websites browsed) used to gather information for this answer. Mandatory if the task required web lookup.",
                        },
                    },
                    "required": ["answer"],
                },
            },
        }
    ]


TOOL_REGISTRY: Dict[str, ToolHandler] = {
    "browse_web": browse_web,
    "search_web": search_web,
    "extract_data": extract_data,
    "navigate_page": navigate_page,
    "finish_task": finish_task,
}


async def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a registered tool by name with the given arguments."""
    try:
        handler = TOOL_REGISTRY[name]
    except KeyError as exc:
        logger.error(f"Unknown tool: {name}")
        raise ValueError(f"Unknown tool: {name}") from exc

    # Filter arguments to match the handler's signature to prevent TypeError: unexpected keyword argument
    try:
        sig = inspect.signature(handler)
        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_kwargs:
            valid_args = {k: v for k, v in arguments.items() if k in sig.parameters}
        else:
            valid_args = arguments
    except Exception as e:
        logger.warning(f"Failed to inspect signature of tool {name}: {e}")
        valid_args = arguments

    retries = 3
    for attempt in range(1, retries + 1):
        try:
            result = await handler(**valid_args)
            return result
        except Exception as e:
            logger.warning(f"Tool {name} attempt {attempt} failed: {e}")
            if attempt < retries:
                await asyncio.sleep(1.0)
            else:
                logger.error(f"Tool {name} failed after {retries} attempts: {e}")
                return {"success": False, "error": str(e)}

