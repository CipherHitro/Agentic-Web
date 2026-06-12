import json
import logging
from typing import Any, Dict, List, TypedDict

from app.agents.prompts import WEB_AGENT_SYSTEM_PROMPT
from app.services.llm_service import get_openai_client
from app.config import settings
from app.tools.tool_registry import execute_tool, get_tool_definitions
from app.scraper.browser import browser_manager

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    navigation_depth: int


class AIAgent:
    def __init__(self):
        self.model = settings.openrouter_model
        self.tools = get_tool_definitions()

    def _needs_browse_enforcement(self, messages: List[Dict[str, Any]]) -> bool:
        user_query = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_query = msg.get("content", "").lower()
                break

        if not user_query:
            return True

        simple_qa_triggers = [
            "what year", "when was", "who created", "who is", "who founded",
            "who wrote", "what is the capital", "when did", "how old", "where is",
            "what is the definition", "define "
        ]
        has_simple_qa_trigger = any(trigger in user_query for trigger in simple_qa_triggers)

        browse_keywords = [
            "scrape", "browse", "extract", "visit", "go to", "url", "link", ".com", ".org", ".net", ".io", "http",
            "repositories", "projects", "pricing", "list of", "compare", "features", "details", "full text", "articles", "github"
        ]
        has_browse_keyword = any(kw in user_query for kw in browse_keywords)

        if has_simple_qa_trigger and not has_browse_keyword:
            return False

        return True

    def _is_greeting(self, messages: List[Dict[str, Any]]) -> bool:
        user_query = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_query = msg.get("content", "").strip().lower()
                break
        if not user_query:
            return True
        greetings = {"hi", "hello", "hey", "hola", "yo", "greetings", "good morning", "good afternoon", "good evening"}
        return user_query in greetings

    async def chat(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Main chat loop. Handles tool calling automatically."""
        user_query = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        print(f"\n==================================================")
        print(f"🤖 AGENT CHAT SESSION STARTED")
        print(f"👉 User Query: {user_query}")
        print(f"==================================================\n")
        logger.info(f"Starting chat session with {len(messages)} input messages")
        
        client = get_openai_client()
        full_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": WEB_AGENT_SYSTEM_PROMPT},
            *messages,
        ]

        new_messages = []
        max_steps = 20
        step = 0

        last_tool_used = None
        last_tool_result = None
        last_raw_url = None

        # Track tool calls to enforce search-to-browse and stateful depth limit
        search_web_called = False
        browse_web_succeeded = False
        state = AgentState(navigation_depth=0)

        # Scan initial history to set tracking flags
        for msg in full_messages:
            if msg.get("role") == "tool":
                name = msg.get("name")
                content_str = msg.get("content", "")
                is_success = True
                try:
                    res_json = json.loads(content_str)
                    if isinstance(res_json, dict) and "success" in res_json:
                        is_success = res_json["success"]
                except Exception:
                    pass

                if name == "search_web":
                    search_web_called = True
                elif name == "browse_web" and is_success:
                    browse_web_succeeded = True
                elif name == "navigate_page":
                    state["navigation_depth"] += 1

        # Sync depth with browser manager
        browser_manager.navigation_depth = state["navigation_depth"]

        while step < max_steps:
            step += 1
            logger.info(f"Agent step {step}/{max_steps} - Requesting LLM completion")
            print(f"⏳ [AGENT STEP {step}/{max_steps}] Requesting LLM completion...")
            response = await client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=self.tools,
                tool_choice="auto" if step < max_steps else "none",
                max_tokens=4000,
            )

            message = response.choices[0].message
            if message.content:
                print(f"\n🧠 [AGENT REASONING / PLAN] (Step {step})")
                print(f"--------------------------------------------------")
                print(message.content.strip())
                print(f"--------------------------------------------------\n")

            if not message.tool_calls:
                # 1. Check if the agent is trying to answer without calling any tools at all (unless it's a simple greeting)
                if not search_web_called and not browse_web_succeeded and not self._is_greeting(messages) and step < max_steps:
                    logger.warning("Agent tried to answer without using any tools. Forcing tool call.")
                    print(f"⚠️  [GUARDRAIL WARNING] Agent attempted to answer directly without tools. Injecting correction system message...")
                    warning_msg = {
                        "role": "system",
                        "content": (
                            "Correction: You have attempted to answer using your pre-training/internal knowledge without executing any tool. "
                            "As a Web AI Agent, you are NOT allowed to answer from internal knowledge. You MUST use search_web "
                            "or browse_web to gather real-time data first. "
                            "Please execute your plan by calling the appropriate tool now."
                        )
                    }
                    full_messages.append(warning_msg)
                    new_messages.append(warning_msg)
                    continue

                # 2. Check if search was called but browse did not succeed
                if self._needs_browse_enforcement(messages) and search_web_called and not browse_web_succeeded and step < max_steps:
                    logger.warning("Agent tried to answer without a successful browse_web. Forcing browse_web.")
                    print(f"⚠️  [GUARDRAIL WARNING] Agent searched but tried to answer without successful browse. Injecting browse warning...")
                    warning_msg = {
                        "role": "system",
                        "content": (
                            "Correction: You have only searched the web or had failed browse attempts, but have not successfully browsed and read the actual "
                            "webpages. Snippets from search_web are incomplete and not acceptable as a final answer. "
                            "You MUST call browse_web on the relevant URL discovered to read the full page details "
                            "before writing your final response."
                        )
                    }
                    full_messages.append(warning_msg)
                    new_messages.append(warning_msg)
                    # Do not let step count hit max immediately, allow the agent to continue
                    continue

                final_content = message.content or ""
                if not final_content.strip():
                    logger.warning("Agent returned an empty text response with no tool calls. Injecting recovery prompt.")
                    print("⚠️  [GUARDRAIL WARNING] Agent returned an empty text response. Injecting recovery prompt...")
                    recovery_msg = {
                        "role": "system",
                        "content": (
                            "Correction: Your previous response was empty. You must either continue the task by calling "
                            "tools, or if you have all the information, output the final factual answer with source URLs. "
                            "Do not leave the response blank."
                        )
                    }
                    full_messages.append(recovery_msg)
                    new_messages.append(recovery_msg)
                    continue

                logger.info(f"LLM decided to respond directly without tool calls. Content length: {len(final_content)}")
                print(f"\n==================================================")
                print(f"✅ AGENT TASK COMPLETED SUCCESSFULLY")
                print(f"🏁 Final Response:\n{final_content}")
                print(f"==================================================\n")
                assistant_final_msg = {
                    "role": "assistant",
                    "content": final_content,
                }
                new_messages.append(assistant_final_msg)

                return {
                    "response": final_content,
                    "tool_used": last_tool_used,
                    "tool_result": last_tool_result,
                    "raw_url": last_raw_url,
                    "new_messages": new_messages,
                }

            logger.info(f"LLM requested {len(message.tool_calls)} tool call(s) at step {step}")
            print(f"🛠️  [TOOL CALL REQUEST] LLM requested {len(message.tool_calls)} tool call(s) at step {step}")
            assistant_msg = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": []
            }

            tool_msgs = []

            tools_in_step = []
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                # Truncate args for cleaner print
                logged_args = {}
                for k, v in tool_args.items():
                    if isinstance(v, str) and len(v) > 200:
                        logged_args[k] = v[:200] + f"... [Truncated, total {len(v)} characters]"
                    else:
                        logged_args[k] = v

                print(f"👉 [EXECUTING TOOL] {tool_name} with args: {logged_args}")
                logger.info(f"Executing tool '{tool_name}' with args: {logged_args}")
                tool_result = await execute_tool(tool_name, tool_args)
                is_success = tool_result.get("success", False) if isinstance(tool_result, dict) else True
                print(f"🏁 [TOOL COMPLETED] {tool_name}. Success: {is_success}")
                logger.info(f"Tool '{tool_name}' execution completed. Success: {is_success}")

                if tool_name == "search_web":
                    search_web_called = True
                elif tool_name == "browse_web" and is_success:
                    browse_web_succeeded = True
                elif tool_name == "navigate_page" and is_success:
                    state["navigation_depth"] += 1

                tools_in_step.append(tool_name)
                last_tool_used = ", ".join(tools_in_step)
                last_tool_result = tool_result
                last_raw_url = tool_args.get("url") if isinstance(tool_args, dict) else last_raw_url

                assistant_msg["tool_calls"].append({
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_args),
                    }
                })

                tool_msgs.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": json.dumps(tool_result),
                })

            new_messages.append(assistant_msg)
            full_messages.append(assistant_msg)

            for tool_msg in tool_msgs:
                new_messages.append(tool_msg)
                full_messages.append(tool_msg)

        logger.warning(f"Exceeded maximum agent steps ({max_steps}). Returning fallback message.")
        fallback_content = "I have reached the maximum number of browsing steps to complete this request."
        new_messages.append({
            "role": "assistant",
            "content": fallback_content
        })
        return {
            "response": fallback_content,
            "tool_used": last_tool_used,
            "tool_result": last_tool_result,
            "raw_url": last_raw_url,
            "new_messages": new_messages,
        }


agent = AIAgent()
