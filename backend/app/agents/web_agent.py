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

    async def chat(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Main chat loop. Handles tool calling automatically."""
        user_query = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        print(f"\n==================================================")
        print(f"🤖 AGENT CHAT SESSION STARTED")
        print(f"👉 User Query: {user_query}")
        print(f"==================================================\n")
        logger.info(f"Starting chat session with {len(messages)} input messages")
        
        client = get_openai_client()

        # Layer 1: Intent router (cheap, fast, deterministic classification)
        requires_web = True
        try:
            router_prompt = (
                "You are a fast, precise classifier that decides if a user request requires web search/browsing.\n"
                "Classify the latest user query with conversation context into exactly one of these categories:\n"
                "CONVERSATIONAL - greetings, thank you, identity/capabilities, or general chitchat (e.g. 'hello', 'who are you', 'thanks').\n"
                "STATIC_KNOWLEDGE - timeless general knowledge, coding questions, algorithms, math, grammar, or definitions (e.g. 'what is recursion', 'write quicksort in python', 'define photosynthesis'). Internal knowledge is fine.\n"
                "WEB_REQUIRED - anything needing current state of the world, prices, availability, news, release dates, profiles, specific URLs/websites, or time-sensitive data (e.g. 'price of gold', 'weather today', 'latest news', 'list repositories of user X').\n\n"
                "Response: Return ONLY the single word classification (CONVERSATIONAL, STATIC_KNOWLEDGE, or WEB_REQUIRED). Do not explain."
            )
            router_history = [{"role": "system", "content": router_prompt}]
            for msg in messages:
                if msg.get("role") in ("user", "assistant"):
                    router_history.append({"role": msg["role"], "content": msg["content"]})
            
            router_response = await client.chat.completions.create(
                model=self.model,
                messages=router_history,
                max_tokens=15,
                temperature=0.0,
            )
            classification = router_response.choices[0].message.content.strip().upper()
            logger.info(f"Intent classification result: {classification}")
            print(f"🎯 [INTENT ROUTER] Query classified as: {classification}")
            if "CONVERSATIONAL" in classification or "STATIC_KNOWLEDGE" in classification:
                requires_web = False
        except Exception as e:
            logger.error(f"Error in intent classification: {e}. Defaulting to requires_web = True")
            print(f"⚠️  [INTENT ROUTER] Error running classification: {e}. Defaulting to WEB_REQUIRED.")

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
        extract_data_called = False
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
                elif name == "extract_data" and is_success:
                    extract_data_called = True
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

            is_attempting_to_finish = (not message.tool_calls) or any(tc.function.name == "finish_task" for tc in message.tool_calls)

            if is_attempting_to_finish:
                # Construct assistant message structure to append to history
                assistant_msg = {
                    "role": "assistant",
                    "content": message.content or ""
                }
                if message.tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                        for tc in message.tool_calls
                    ]

                # Layer 3: Output verification - if requires_web is True and finish_task has empty/missing sources, reject it
                if requires_web and message.tool_calls and step < max_steps:
                    finish_call = next((tc for tc in message.tool_calls if tc.function.name == "finish_task"), None)
                    if finish_call:
                        try:
                            args = json.loads(finish_call.function.arguments)
                        except Exception:
                            args = {}
                        sources = args.get("sources")
                        if not sources or not isinstance(sources, list) or len(sources) == 0:
                            logger.warning("Agent tried to finish a web-required query without sources.")
                            print("⚠️  [GUARDRAIL WARNING] Agent tried to finish web-required query without sources. Injecting correction...")
                            warning_msg = {
                                "role": "user",
                                "content": (
                                    "[SYSTEM CORRECTION] The user's query requires web search/browsing. "
                                    "You cannot complete the task or call finish_task without listing the browsed source URLs in the 'sources' parameter. "
                                    "Please browse the relevant pages and provide their URLs in the sources list when calling finish_task."
                                )
                            }
                            full_messages.append(assistant_msg)
                            new_messages.append(assistant_msg)
                            full_messages.append(warning_msg)
                            new_messages.append(warning_msg)
                            continue

                # 1. Check if the agent is trying to answer without calling any tools at all (when requires_web is True)
                if requires_web and not search_web_called and not browse_web_succeeded and step < max_steps:
                    logger.warning("Agent tried to finish without using any web tools for a web-required task. Forcing tool call.")
                    print(f"⚠️  [GUARDRAIL WARNING] Agent attempted to finish directly without tools. Injecting correction system message...")
                    warning_msg = {
                        "role": "user",
                        "content": (
                            "[SYSTEM CORRECTION] You have attempted to finish or answer using your pre-training/internal knowledge without executing any tool. "
                            "As a Web AI Agent, you are NOT allowed to answer from internal knowledge. You MUST use search_web "
                            "or browse_web to gather real-time data first. "
                            "Please execute your plan by calling the appropriate tool now."
                        )
                    }
                    full_messages.append(assistant_msg)
                    new_messages.append(assistant_msg)
                    full_messages.append(warning_msg)
                    new_messages.append(warning_msg)
                    continue

                # 2. Check if search was called but browse did not succeed (when requires_web is True)
                if requires_web and search_web_called and not browse_web_succeeded and step < max_steps:
                    logger.warning("Agent tried to finish without a successful browse_web. Forcing browse_web.")
                    print(f"⚠️  [GUARDRAIL WARNING] Agent searched but tried to finish without successful browse. Injecting browse warning...")
                    warning_msg = {
                        "role": "user",
                        "content": (
                            "[SYSTEM CORRECTION] You have only searched the web or had failed browse attempts, but have not successfully browsed and read the actual "
                            "webpages. Snippets from search_web are incomplete and not acceptable as a final answer. "
                            "You MUST call browse_web on the relevant URL discovered to read the full page details "
                            "before writing your final response."
                        )
                    }
                    full_messages.append(assistant_msg)
                    new_messages.append(assistant_msg)
                    full_messages.append(warning_msg)
                    new_messages.append(warning_msg)
                    continue

                # 3. Check if extract_data was not called but we have browsed a page successfully (when requires_web is True)
                if requires_web and not extract_data_called and browse_web_succeeded and step < max_steps:
                    logger.warning("Agent tried to finish without calling extract_data. Forcing extract_data.")
                    print(f"⚠️  [GUARDRAIL WARNING] Agent tried to finish without calling extract_data. Injecting extract_data warning...")
                    warning_msg = {
                        "role": "user",
                        "content": (
                            "[SYSTEM CORRECTION] You have browsed a webpage but have not executed extract_data to extract the structured information yet. "
                            "You must call extract_data(page_content, fields) to pull the specific facts, specs, or details requested before formulating a final answer. "
                            "Please execute the extract_data tool now."
                        )
                    }
                    full_messages.append(assistant_msg)
                    new_messages.append(assistant_msg)
                    full_messages.append(warning_msg)
                    new_messages.append(warning_msg)
                    continue

                # 4. If there are no tool calls, handle plain text responses (reasoning messages)
                if not message.tool_calls:
                    final_content = message.content or ""
                    
                    # Layer 2: Accept plain-text direct replies for non-web tasks immediately as final response!
                    if not requires_web:
                        print(f"🏁 [AGENT RESPONSE] Plain text accepted as final response.")
                        return {
                            "response": final_content,
                            "tool_used": "plain_text",
                            "tool_result": None,
                            "raw_url": None,
                            "new_messages": new_messages + [assistant_msg],
                        }
                    
                    if not final_content.strip():
                        logger.warning("Agent returned an empty response. Injecting recovery prompt.")
                        print("⚠️  [GUARDRAIL WARNING] Agent returned an empty response. Injecting recovery prompt...")
                        recovery_msg = {
                            "role": "user",
                            "content": (
                                "[SYSTEM CORRECTION] Your previous response was empty. You must either continue the task by calling "
                                "tools, or if you have all the information, output the final factual answer using finish_task(answer). "
                                "Do not leave the response blank."
                            )
                        }
                        full_messages.append(assistant_msg)
                        new_messages.append(assistant_msg)
                        full_messages.append(recovery_msg)
                        new_messages.append(recovery_msg)
                        continue

                    logger.info("Agent provided mid-task reasoning without tool calls. Nudging agent to continue.")
                    print("💬 [AGENT STATE] Mid-task reasoning or planning emitted without a tool call. Injecting nudge...")
                    
                    nudge_msg = {
                        "role": "user",
                        "content": (
                            "[SYSTEM NOTICE] You did not call a tool. If you have completed the user's request, or the request needs no research, call finish_task with your reply. Otherwise continue your plan."
                        )
                    }
                    full_messages.append(assistant_msg)
                    full_messages.append(nudge_msg)
                    new_messages.append(assistant_msg)
                    new_messages.append(nudge_msg)
                    continue

            logger.info(f"LLM requested {len(message.tool_calls)} tool call(s) at step {step}")
            print(f"🛠️  [TOOL CALL REQUEST] LLM requested {len(message.tool_calls)} tool call(s) at step {step}")
            assistant_msg = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": []
            }

            tool_msgs = []

            tools_in_step = []
            finished = False
            final_answer = ""

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
                elif tool_name == "extract_data" and is_success:
                    extract_data_called = True
                elif tool_name == "navigate_page" and is_success:
                    state["navigation_depth"] += 1
                elif tool_name == "finish_task" and is_success:
                    finished = True
                    final_answer = tool_args.get("answer", "") or tool_result.get("answer", "")

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

            if finished:
                print(f"\n==================================================")
                print(f"✅ AGENT TASK COMPLETED SUCCESSFULLY via finish_task")
                print(f"🏁 Final Response:\n{final_answer}")
                print(f"==================================================\n")
                return {
                    "response": final_answer,
                    "tool_used": last_tool_used,
                    "tool_result": last_tool_result,
                    "raw_url": last_raw_url,
                    "new_messages": new_messages,
                }

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
