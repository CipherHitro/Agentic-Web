import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List

from app.tools.browser_tools import browse_web
from app.tools.search_tools import search_web
from app.tools.extraction_tools import extract_data
from app.tools.navigation_tools import navigate_page, get_current_url, go_back
from app.tools.finish_tool import finish_task
from app.tools.click_tools import click_element
from app.tools.fill_tools import fill_form_field
from app.tools.form_inspect_tools import read_form_fields
from app.tools.select_tools import select_form_option
from app.tools.scroll_tools import scroll
from app.tools.screenshot_tools import take_screenshot
from app.tools.human_tools import request_human_input
from app.tools.observe_tools import observe_page

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Awaitable[Dict[str, Any]]]


def get_tool_definitions() -> List[Dict[str, Any]]:
    return [
        # ── 1. search_web ──────────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": (
                    "Search the internet and return a ranked list of URLs with titles and snippets. "
                    "Use this to discover URLs when you don't have a direct link. "
                    "Snippets are NOT a final answer — always follow up with browse_web on the best URL. "
                    "Use the user's exact terms in the query. "
                    "Do NOT use this to replace browse_web when the user explicitly names a website."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query. Use natural language with the user's exact terms.",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of results to return. Default 5.",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
        },

        # ── 2. browse_web ──────────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "browse_web",
                "description": (
                    "Load any URL in a fresh browser session and return the page text and links. "
                    "Use this to: open a specific website, jump to a new domain, or try a direct URL "
                    "when navigation fails (e.g. browse_web('https://example.com/logout') instead of "
                    "clicking a link that keeps failing). "
                    "Each call starts a fresh browser context. "
                    "Use navigate_page to go deeper within the current site."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The full URL including https://",
                        },
                        "extract_content": {
                            "type": "boolean",
                            "description": "Whether to extract the main text content. Default true.",
                            "default": True,
                        },
                        "scroll_page": {
                            "type": "boolean",
                            "description": "Whether to scroll the page to load lazy content. Default false.",
                            "default": False,
                        },
                    },
                    "required": ["url"],
                },
            },
        },

        # ── 3. navigate_page ───────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "navigate_page",
                "description": (
                    "Follow a link or open a sub-page within the CURRENT browser session. "
                    "Use for going deeper into the same site: clicking a tab, opening a listing, "
                    "going to page 2, following a sidebar link, or entering a sub-section. "
                    "Describe your intent in plain language naming the target "
                    "(e.g. 'open the repositories tab', 'go to settings', 'click the first result'). "
                    "Do NOT use this to jump to a different domain — use browse_web for that."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": (
                                "What you want to navigate to. Name the target section, link, or tab "
                                "(e.g. 'go to the about section', 'open page 2', 'click settings link')."
                            ),
                        },
                    },
                    "required": ["intent"],
                },
            },
        },

        # ── 4. click_element ───────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "click_element",
                "description": (
                    "Click a button, toggle, dropdown trigger, modal opener, or any interactive element "
                    "that is NOT a plain navigation link. "
                    "Describe the target using its visible text, aria-label, or role "
                    "(e.g. 'Submit button', 'Accept cookies', 'profile avatar to open user menu', "
                    "'gear icon next to About section', 'Save changes button'). "
                    "IMPORTANT: If the target element lives inside a dropdown, popup, or modal, "
                    "you must first click the trigger that opens it, then call click_element again "
                    "for the item inside. Never assume a dropdown item is clickable without opening "
                    "the dropdown first. "
                    "If this call fails, call observe_page to re-assess and try a different intent."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": (
                                "Describe the element to click using visible text, label, or role. "
                                "Be specific: 'profile avatar' not just 'button', "
                                "'gear icon next to About section' not just 'settings'."
                            ),
                        },
                    },
                    "required": ["intent"],
                },
            },
        },

        # ── 5. fill_form_field ─────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "fill_form_field",
                "description": (
                    "Type text into an input, textarea, or contenteditable field on the current page. "
                    "Use the field's visible label or question text as field_description. "
                    "IMPORTANT: If the input field does not exist yet on the page (because it is "
                    "hidden inside a modal, settings panel, or edit dialog), you MUST first open "
                    "that panel with click_element before calling this. "
                    "Call observe_page first if you are unsure whether the field is currently visible. "
                    "If this call fails, call observe_page to re-assess."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "field_description": {
                            "type": "string",
                            "description": (
                                "The visible label or question text for the field "
                                "(e.g. 'Short description', 'What is your name?', 'Email address'). "
                                "Do NOT use generic placeholders like 'input' or 'text box'."
                            ),
                        },
                        "value": {
                            "type": "string",
                            "description": "The text to type into the field.",
                        },
                    },
                    "required": ["field_description", "value"],
                },
            },
        },

        # ── 6. read_form_fields ────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "read_form_fields",
                "description": (
                    "Scan the current page and return every visible form question with its type "
                    "(text / radio_or_rating / checkbox) and all available options. "
                    "ALWAYS call this FIRST before filling any form — before any fill_form_field "
                    "or select_form_option call. Never fill a form without reading it first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },

        # ── 7. select_form_option ──────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "select_form_option",
                "description": (
                    "Select a radio button, checkbox, scale value, rating, or dropdown option. "
                    "question = the exact question text returned by read_form_fields. "
                    "option = the exact option label to select (e.g. 'Yes', 'Option A', '3'). "
                    "For checkbox questions that need multiple values selected, call this once per value. "
                    "NEVER use fill_form_field for radio, checkbox, scale, or rating questions — "
                    "there is no text box for those, and fill_form_field will overwrite an unrelated field."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The exact question text from read_form_fields.",
                        },
                        "option": {
                            "type": "string",
                            "description": "The exact option label to select.",
                        },
                    },
                    "required": ["question", "option"],
                },
            },
        },

        # ── 8. scroll ──────────────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "scroll",
                "description": (
                    "Scroll the current page. Use 'down'/'up' for one viewport, "
                    "'top' to jump to page header (find edit buttons after reading README), "
                    "'bottom' to reach page footer. Max 3 scrolls per page before switching strategy."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "top", "bottom"],
                            "description": "Scroll direction. Use 'top' when edit controls are above the fold.",
                        },
                    },
                    "required": [],
                },
            },
        },

        # ── 9. get_current_url ─────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "get_current_url",
                "description": (
                    "Return the URL of the active browser page. "
                    "Use to verify navigation succeeded, or to discover where the browser is "
                    "after a human logs in (so you can find the username or profile URL "
                    "from the actual page rather than guessing)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },

        # ── 10. go_back ────────────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "go_back",
                "description": (
                    "Navigate to the previous page in browser history. "
                    "Use when navigate_page or click_element landed on the wrong page "
                    "and you want to try again from the previous page."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },

        # ── 11. take_screenshot ────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "take_screenshot",
                "description": (
                    "Capture a screenshot of the current page and analyze it visually. "
                    "Use this when: "
                    "(a) a click or fill failed and you need to visually understand what is on screen, "
                    "(b) observe_page returned unclear guidance, "
                    "(c) you want to verify a task completed successfully (e.g. form submitted, change saved). "
                    "After taking a screenshot, use the visual context to re-plan your next action."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },

        # ── 12. extract_data ───────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "extract_data",
                "description": (
                    "Extract specific named fields from the current page content. "
                    "Use after browse_web or navigate_page to pull structured information "
                    "(prices, names, descriptions, counts, dates, etc.). "
                    "Returns null per field if not found — never invents values. "
                    "If a field comes back null, navigate to a sub-page or try a different URL."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Field names or descriptions to extract "
                                "(e.g. ['product price', 'description', 'star count'])."
                            ),
                        },
                    },
                    "required": ["fields"],
                },
            },
        },

        # ── 13. observe_page ───────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "observe_page",
                "description": (
                    "Analyze the current page's visible interactive elements (buttons, inputs, links, "
                    "dropdowns, modals) and return a step-by-step action plan to achieve the given goal "
                    "based on what is ACTUALLY visible in the DOM right now. "
                    "Use this BEFORE click_element or fill_form_field when: "
                    "(1) the target might be inside a dropdown, popup, or settings panel, "
                    "(2) a previous click or fill failed, "
                    "(3) you are unsure what trigger to click to reveal a hidden field or menu. "
                    "The plan it returns will tell you what to click first to reveal hidden elements. "
                    "Always follow the plan step by step after calling this."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": (
                                "The specific objective you want to achieve on this page "
                                "(e.g. 'find and click the logout option', "
                                "'open the edit panel for the repository description', "
                                "'locate the save button after filling the form')."
                            ),
                        },
                    },
                    "required": ["goal"],
                },
            },
        },

        # ── 14. request_human_input ────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "request_human_input",
                "description": (
                    "Pause the agent and show a message to the human. "
                    "Use ONLY for: login/authentication pages, MFA/2FA challenges, captchas, "
                    "or cases where the task genuinely cannot proceed without a human action. "
                    "Write a clear prompt telling the human exactly what to do and what to do next "
                    "(e.g. 'Please log in on the browser screen. Once you are logged in, type Done here.'). "
                    "After receiving the human's reply, immediately continue the task — "
                    "do NOT re-plan from scratch. First call get_current_url to understand where "
                    "the browser is, then extract data from the current page to discover any "
                    "account-specific information (like username or profile URL) before navigating further. "
                    "NEVER use this to ask for clarification on non-auth matters."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "The message shown to the human. Be specific: explain what they need "
                                "to do on the browser screen and what they should type back when done."
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
            },
        },

        # ── 15. finish_task ────────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": (
                    "Submit the final answer and end the task. "
                    "Call this ONLY when the full task is complete — every step the user asked for "
                    "has been executed and verified. "
                    "For multi-step tasks, all steps must be done before calling this. "
                    "answer = the direct, complete, factual result. "
                    "sources = every URL browsed. Mandatory for any web task. "
                    "If some steps could not be completed after exhausting all strategies, "
                    "state clearly what was tried and what the outcome was. "
                    "NEVER call this early just because one step failed — try all recovery strategies first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {
                            "type": "string",
                            "description": "The complete result of the task. Describe every step that was done and its outcome.",
                        },
                        "sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "All URLs browsed during this task. Required for web tasks.",
                        },
                    },
                    "required": ["answer"],
                },
            },
        },
    ]


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOL_REGISTRY: Dict[str, ToolHandler] = {
    "browse_web": browse_web,
    "search_web": search_web,
    "extract_data": extract_data,
    "navigate_page": navigate_page,
    "finish_task": finish_task,
    "click_element": click_element,
    "fill_form_field": fill_form_field,
    "read_form_fields": read_form_fields,
    "select_form_option": select_form_option,
    "scroll": scroll,
    "get_current_url": get_current_url,
    "go_back": go_back,
    "take_screenshot": take_screenshot,
    "request_human_input": request_human_input,
    "observe_page": observe_page,
}


async def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a registered tool by name with the given arguments."""
    try:
        handler = TOOL_REGISTRY[name]
    except KeyError as exc:
        logger.error(f"Unknown tool: {name}")
        raise ValueError(f"Unknown tool: {name}") from exc

    # Filter arguments to only those the handler accepts (prevents TypeError on extra kwargs)
    try:
        sig = inspect.signature(handler)
        has_var_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        valid_args = arguments if has_var_kwargs else {
            k: v for k, v in arguments.items() if k in sig.parameters
        }
    except Exception as e:
        logger.warning(f"Could not inspect signature of tool '{name}': {e}")
        valid_args = arguments

    retries = 3
    for attempt in range(1, retries + 1):
        try:
            return await handler(**valid_args)
        except Exception as e:
            logger.warning(f"Tool '{name}' attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                await asyncio.sleep(1.0)
            else:
                logger.error(f"Tool '{name}' failed after {retries} attempts: {e}")
                return {"success": False, "error": str(e)}