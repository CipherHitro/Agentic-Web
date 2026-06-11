import logging
from typing import Any, Awaitable, Callable, Dict, List

from app.tools.browser_tools import browse_web
from app.tools.search_tools import search_web

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
        }
    ]


TOOL_REGISTRY: Dict[str, ToolHandler] = {
    "browse_web": browse_web,
    "search_web": search_web,
}


async def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    print(f"\n>>> [AI TOOL EXECUTION] Tool: '{name}' | Args: {arguments}")
    logger.info(f"Executing tool {name} with arguments {arguments}")
    try:
        handler = TOOL_REGISTRY[name]
    except KeyError as exc:
        print(f"!!! [AI TOOL ERROR] Unknown tool: '{name}'")
        logger.error(f"Unknown tool: {name}")
        raise ValueError(f"Unknown tool: {name}") from exc

    try:
        result = await handler(**arguments)
        print(f"<<< [AI TOOL COMPLETED] Tool: '{name}' | Success\n")
        logger.info(f"Tool {name} completed successfully")
        return result
    except Exception as e:
        print(f"!!! [AI TOOL ERROR] Tool: '{name}' failed: {e}\n")
        logger.error(f"Tool {name} failed: {e}")
        raise
