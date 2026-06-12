import json
import logging
from typing import Any, Dict, List, TypedDict
from datetime import datetime, timezone

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
        task_anchor = (
            f"\n\nREMINDER OF ORIGINAL TASK: \"{user_query}\". "
            f"You must answer THIS exact question. Do not substitute it with a different "
            f"product, topic, or question. If the exact item cannot be found after a real "
            f"web investigation, state that explicitly instead of answering something else."
        )
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
        
        current_date = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
        system_content = WEB_AGENT_SYSTEM_PROMPT + (
                f"\n\n══════════════════════════════════════════════════\n"
                f"TEMPORAL CONTEXT\n"
                f"══════════════════════════════════════════════════\n\n"
                f"Today's date is {current_date}. Your training data has a cutoff and is "
                f"MONTHS OR YEARS OUT OF DATE relative to today. Products, events, and websites "
                f"that did not exist in your training data may exist now. Live web page content "
                f"reflects the present; your memory reflects the past."
            )
        full_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_content},
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
                max_tokens=2000,
            )

            message = response.choices[0].message
            if message.content:
                print(f"\n🧠 [AGENT REASONING / PLAN] (Step {step})")
                print(f"--------------------------------------------------")
                print(message.content.strip())
                print(f"--------------------------------------------------\n")

            # Check if finish_task is being called
            finish_call = None
            if message.tool_calls:
                finish_call = next((tc for tc in message.tool_calls if tc.function.name == "finish_task"), None)

            # If calling finish_task, run the enforcements
            if finish_call and step < max_steps:
                # Construct assistant message structure to append to history
                assistant_msg = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
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
                }
                
                try:
                    args = json.loads(finish_call.function.arguments)
                except Exception:
                    args = {}

                # Guardrails for web-required queries
                if requires_web:
                    # 1. Sources check
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
                                + task_anchor
                            )
                        }
                        full_messages.append(assistant_msg)
                        new_messages.append(assistant_msg)
                        full_messages.append(warning_msg)
                        new_messages.append(warning_msg)
                        continue

                    # 2. No-tools check (did not search and did not browse)
                    if not search_web_called and not browse_web_succeeded:
                        logger.warning("Agent tried to finish without using any web tools for a web-required task. Forcing tool call.")
                        print(f"⚠️  [GUARDRAIL WARNING] Agent attempted to finish directly without tools. Injecting correction system message...")
                        warning_msg = {
                            "role": "user",
                            "content": (
                                "[SYSTEM CORRECTION] You have attempted to finish or answer using your pre-training/internal knowledge without executing any tool. "
                                "As a Web AI Agent, you are NOT allowed to answer from internal knowledge. You MUST use search_web "
                                "or browse_web to gather real-time data first. "
                                "Please execute your plan by calling the appropriate tool now."
                                + task_anchor
                            )
                        }
                        full_messages.append(assistant_msg)
                        new_messages.append(assistant_msg)
                        full_messages.append(warning_msg)
                        new_messages.append(warning_msg)
                        continue

                    # 3. Browse check (searched but did not browse)
                    if search_web_called and not browse_web_succeeded:
                        logger.warning("Agent tried to finish without a successful browse_web. Forcing browse_web.")
                        print(f"⚠️  [GUARDRAIL WARNING] Agent searched but tried to finish without successful browse. Injecting browse warning...")
                        warning_msg = {
                            "role": "user",
                            "content": (
                                "[SYSTEM CORRECTION] You have only searched the web or had failed browse attempts, but have not successfully browsed and read the actual "
                                "webpages. Snippets from search_web are incomplete and not acceptable as a final answer. "
                                "You MUST call browse_web on the relevant URL discovered to read the full page details "
                                "before writing your final response."
                                + task_anchor
                            )
                        }
                        full_messages.append(assistant_msg)
                        new_messages.append(assistant_msg)
                        full_messages.append(warning_msg)
                        new_messages.append(warning_msg)
                        continue

                    # 4. Extract check (browsed but did not extract)
                    if browse_web_succeeded and not extract_data_called:
                        logger.warning("Agent tried to finish without calling extract_data. Forcing extract_data.")
                        print(f"⚠️  [GUARDRAIL WARNING] Agent tried to finish without calling extract_data. Injecting extract_data warning...")
                        warning_msg = {
                            "role": "user",
                            "content": (
                                "[SYSTEM CORRECTION] You have browsed a webpage but have not executed extract_data to extract the structured information yet. "
                                "You must call extract_data(page_content, fields) to pull the specific facts, specs, or details requested before formulating a final answer. "
                                "Please execute the extract_data tool now."
                                + task_anchor
                            )
                        }
                        full_messages.append(assistant_msg)
                        new_messages.append(assistant_msg)
                        full_messages.append(warning_msg)
                        new_messages.append(warning_msg)
                        continue

                # 5. Relevance check on finish_task
                verdict_resp = await client.chat.completions.create(
                    model=self.model,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"Original question: \"{user_query}\"\n"
                            f"Proposed answer: \"{args.get('answer','')}\"\n\n"
                            "Does the proposed answer address the subject of the original question (rather than substituting it with a different product, topic, or question)? Explain your reasoning, and then reply with YES or NO."
                        )
                    }],
                    max_tokens=150,
                    temperature=0.0,
                )
                verdict_raw = verdict_resp.choices[0].message.content.strip()
                verdict = verdict_raw.upper()
                print(f"🕵️  [RELEVANCE CHECK] Verdict: '{verdict_raw}' for answer: '{args.get('answer','')[:60]}...'")
                if "YES" not in verdict:
                    logger.warning(f"Relevance check failed: {verdict}. Agent's proposed answer does not address the exact subject.")
                    print("⚠️  [GUARDRAIL WARNING] Agent's proposed answer does not address the exact subject. Injecting correction...")
                    warning_msg = {
                        "role": "user",
                        "content": (
                            "[SYSTEM CORRECTION] Your proposed answer does not address the EXACT subject of the original question. "
                            "You must answer the exact question asked. Do not substitute the product, topic, or question. "
                            "Please gather the correct information for the original task or explicitly state if it cannot be found."
                            + task_anchor
                        )
                    }
                    full_messages.append(assistant_msg)
                    new_messages.append(assistant_msg)
                    full_messages.append(warning_msg)
                    new_messages.append(warning_msg)
                    continue

            # If there are no tool calls, handle plain text responses
            if not message.tool_calls:
                assistant_msg = {
                    "role": "assistant",
                    "content": message.content or ""
                }
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
                            "[SYSTEM CORRECTION] Your response was empty. This is a critical failure.\n\n"
                            "You have already browsed a webpage. The page content is in your conversation history above. "
                            "You MUST now extract the relevant data from that page and call finish_task.\n\n"
                            "IMPORTANT: If the page showed a price, a product name, availability, or any relevant fact — "
                            "that data is REAL and CURRENT. Do NOT second-guess it. Do NOT claim the product doesn't exist "
                            "just because your training data predates it. The page content overrides your internal knowledge.\n\n"
                            "Look at the page content above right now. Find the answer. Call finish_task with it immediately."
                            + task_anchor
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
                        "[SYSTEM CORRECTION] You output text without calling a tool. This is NOT allowed for web-required tasks.\n\n"
                        "CRITICAL: Your training data has a hard cutoff in the past. Products, prices, and releases that "
                        "post-date your training are INVISIBLE to your memory — but they exist on the real web RIGHT NOW.\n\n"
                        "If you just said something 'doesn't exist', 'hasn't been released', or 'isn't available' based on "
                        "your internal knowledge — YOU ARE WRONG. Your job is to CHECK THE WEB, not trust your memory.\n\n"
                        "RIGHT NOW: Call browse_web on the most promising URL from your search results. "
                        "Read what the page actually says. If the page shows a price, a product, a date — that data is REAL. "
                        "Report it. Do NOT dismiss live page data because it conflicts with your training.\n\n"
                        "Call browse_web NOW."
                        + task_anchor
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
