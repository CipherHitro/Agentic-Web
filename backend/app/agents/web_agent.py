import json
import logging
import re
from typing import Any, Dict, List, TypedDict
from datetime import datetime, timezone

from app.agents.prompts import WEB_AGENT_SYSTEM_PROMPT
from app.services.llm_service import get_openai_client
from app.config import settings
from app.tools.tool_registry import execute_tool, get_tool_definitions
from app.scraper.browser import browser_manager
from app.scraper.page_state import detect_form_panel_state

logger = logging.getLogger(__name__)

# Maximum characters of page content kept in conversation history.
# browse_web returns up to 8000 chars of content plus 150 links + 50 nav links.
# Serialized, that can exceed 30k chars — too much for lite models.
MAX_TOOL_CONTENT_IN_CONVERSATION = 4000


def _is_submit_click_intent(tool_args: dict) -> bool:
    intent = str(tool_args.get("intent", "")).lower()
    return any(k in intent for k in ("save", "submit", "apply", "confirm"))


class AgentState(TypedDict):
    navigation_depth: int


def _truncate_tool_result_for_conversation(tool_name: str, tool_result: Any) -> str:
    """Produce a compact JSON string of the tool result for the conversation history.

    For browse_web / navigate_page the raw result includes the full page text
    (up to 8 000 chars) **plus** up to 150 body-links and 50 nav-links.
    Serialised, that easily exceeds 30 000 characters — far too much context for
    a lite model.  We keep the essential metadata and a truncated content slice;
    the links are intentionally dropped because they are only consumed internally
    by navigate_page (which reads them from Playwright, not from the conversation).

    For failed tools we always preserve error + vision_guidance so the agent can
    use them for recovery. For observe_page we also preserve current_url / page_title
    so the agent can detect if it landed on the wrong page.
    """
    if not isinstance(tool_result, dict):
        return json.dumps(tool_result)

    if tool_name in ("browse_web", "navigate_page"):
        slim = {
            "url": tool_result.get("url"),
            "title": tool_result.get("title"),
            "status": tool_result.get("status"),
            "success": tool_result.get("success"),
        }
        content = tool_result.get("content", "")
        if isinstance(content, str) and len(content) > MAX_TOOL_CONTENT_IN_CONVERSATION:
            slim["content"] = (
                content[:MAX_TOOL_CONTENT_IN_CONVERSATION]
                + f"\n\n[Truncated — original {len(content)} chars]"
            )
        else:
            slim["content"] = content

        if not tool_result.get("success"):
            slim["error"] = tool_result.get("error")
        return json.dumps(slim)

    # For observe_page: always preserve current_url and page_title so the agent
    # can detect wrong-page navigations, plus the action_plan and vision_guidance.
    if tool_name == "observe_page":
        slim = {
            "success": tool_result.get("success"),
            "current_url": tool_result.get("current_url"),
            "page_title": tool_result.get("page_title"),
            "visible_elements_count": tool_result.get("visible_elements_count"),
            "action_plan": tool_result.get("action_plan"),
            "next_action": tool_result.get("next_action"),
            "instruction": tool_result.get("instruction"),
            "scrolled_to_top": tool_result.get("scrolled_to_top"),
            "elements_snapshot": tool_result.get("elements_snapshot"),
        }
        vision = tool_result.get("vision_guidance", "")
        if isinstance(vision, str) and len(vision) > 1000:
            slim["vision_guidance"] = vision[:1000] + "…"
        else:
            slim["vision_guidance"] = vision
        return json.dumps(slim)

    # For ALL other tools: keep the full result but always preserve error and
    # vision_guidance (key for click/fill failure recovery).
    result_copy = dict(tool_result)
    serialized = json.dumps(result_copy)
    # If the serialized result is reasonable size, return as-is
    if len(serialized) <= 3000:
        return serialized
    # Otherwise slim it down but keep the critical fields
    slim = {
        "success": tool_result.get("success"),
        "error": tool_result.get("error"),
        "vision_guidance": tool_result.get("vision_guidance"),
        "message": tool_result.get("message"),
    }
    # Add any extra fields that fit
    for k, v in tool_result.items():
        if k not in slim and isinstance(v, (str, int, float, bool)) and len(str(v)) < 500:
            slim[k] = v
    return json.dumps({k: v for k, v in slim.items() if v is not None})


class AIAgent:
    def __init__(self):
        self.model = settings.openrouter_model
        self.tools = get_tool_definitions()

    async def chat(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Main chat loop. Handles tool calling automatically."""
        user_query = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        task_anchor = (
            f'\n\nREMINDER OF ORIGINAL TASK: "{user_query}". '
            f"You must answer THIS exact question. Do not substitute it with a different "
            f"product, topic, or question. If the exact item cannot be found after a real "
            f"web investigation, state that explicitly instead of answering something else."
        )

        print(f"\n{'=' * 50}")
        print(f"🤖 AGENT CHAT SESSION STARTED")
        print(f"👉 User Query: {user_query}")
        print(f"{'=' * 50}\n")

        client = get_openai_client()

        # ── Layer 1: Intent router ────────────────────────────────────
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
                    router_history.append(
                        {"role": msg["role"], "content": msg["content"]}
                    )

            router_response = await client.chat.completions.create(
                model=self.model,
                messages=router_history,
                max_tokens=15,
                temperature=0.0,
            )
            classification = (
                router_response.choices[0].message.content.strip().upper()
            )
            print(f"🎯 [INTENT] {classification}")
            if "CONVERSATIONAL" in classification or "STATIC_KNOWLEDGE" in classification:
                requires_web = False
        except Exception as e:
            logger.error(f"Intent classification error: {e}")
            print(f"⚠️  [INTENT] Classification failed, defaulting to WEB_REQUIRED")

        # ── Build conversation ────────────────────────────────────────
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

        new_messages: List[Dict[str, Any]] = []
        max_steps = 20
        step = 0

        last_tool_used = None
        last_tool_result = None
        last_raw_url = None

        # ── Tracking flags (isolated to current query) ────────────────
        search_web_called = False
        browse_web_succeeded = False
        extract_data_called = False
        extract_data_guardrail_fired = False
        consecutive_failures = 0  # unified counter: empty responses + text-only nudges
        consecutive_tool_failures = 0
        last_action_signature = None
        repeat_count = 0
        consecutive_observe_only = 0
        last_observe_result: Dict[str, Any] = {}
        form_panel_open = False
        state = AgentState(navigation_depth=0)

        # Scan only messages after the last user query to avoid contamination
        last_user_idx = -1
        for i in range(len(full_messages) - 1, -1, -1):
            if full_messages[i].get("role") == "user":
                last_user_idx = i
                break

        for msg in full_messages[last_user_idx + 1 :] if last_user_idx != -1 else []:
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

        browser_manager.navigation_depth = state["navigation_depth"]

        # ── Main agent loop ───────────────────────────────────────────
        while step < max_steps:
            step += 1
            print(f"⏳ [STEP {step}/{max_steps}]")
            response = await client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=self.tools,
                tool_choice="auto" if step < max_steps else "none",
                max_tokens=2000,
                temperature=0.0,
            )

            message = response.choices[0].message

            # Log reasoning (truncated for readability)
            if message.content:
                preview = message.content.strip().replace("\n", " ")
                if len(preview) > 200:
                    preview = preview[:200] + "…"
                print(f"   🧠 {preview}")

            # ── finish_task guardrails ────────────────────────────────
            finish_call = None
            if message.tool_calls:
                finish_call = next(
                    (
                        tc
                        for tc in message.tool_calls
                        if tc.function.name == "finish_task"
                    ),
                    None,
                )

            if finish_call and step < max_steps:
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
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }

                try:
                    args = json.loads(finish_call.function.arguments)
                except Exception:
                    args = {}

                if requires_web:
                    # 1. Sources check
                    sources = args.get("sources")
                    if (
                        not sources
                        or not isinstance(sources, list)
                        or len(sources) == 0
                    ):
                        print(
                            "⚠️  [GUARDRAIL] finish_task rejected: missing sources"
                        )
                        warning_msg = {
                            "role": "user",
                            "content": (
                                "[SYSTEM CORRECTION] You cannot call finish_task without "
                                "listing the browsed source URLs in the 'sources' parameter."
                                + task_anchor
                            ),
                        }
                        full_messages += [assistant_msg, warning_msg]
                        new_messages += [assistant_msg, warning_msg]
                        continue

                    # 2. No-tools check
                    if not search_web_called and not browse_web_succeeded:
                        print(
                            "⚠️  [GUARDRAIL] finish_task rejected: no web tools used"
                        )
                        warning_msg = {
                            "role": "user",
                            "content": (
                                "[SYSTEM CORRECTION] You must use search_web or browse_web "
                                "before finishing. You cannot answer from internal knowledge."
                                + task_anchor
                            ),
                        }
                        full_messages += [assistant_msg, warning_msg]
                        new_messages += [assistant_msg, warning_msg]
                        continue

                    # 3. Browse check
                    if search_web_called and not browse_web_succeeded:
                        print(
                            "⚠️  [GUARDRAIL] finish_task rejected: searched but never browsed"
                        )
                        warning_msg = {
                            "role": "user",
                            "content": (
                                "[SYSTEM CORRECTION] Search snippets are not enough. "
                                "You MUST call browse_web on a URL from your search results."
                                + task_anchor
                            ),
                        }
                        full_messages += [assistant_msg, warning_msg]
                        new_messages += [assistant_msg, warning_msg]
                        continue


            # ── No tool calls: handle plain text / failures ───────────
            if not message.tool_calls:
                assistant_msg = {
                    "role": "assistant",
                    "content": message.content or "",
                }
                final_content = message.content or ""

                # Non-web tasks: accept plain text immediately
                if not requires_web:
                    print(f"🏁 [DONE] Plain text response accepted.")
                    return {
                        "response": final_content,
                        "tool_used": "plain_text",
                        "tool_result": None,
                        "raw_url": None,
                        "new_messages": new_messages + [assistant_msg],
                    }

                # ── Unified failure counter ───────────────────────────
                consecutive_failures += 1
                is_empty = not final_content.strip()

                if is_empty:
                    print(
                        f"⚠️  [FAILURE #{consecutive_failures}] Empty response from model"
                    )
                else:
                    print(
                        f"⚠️  [FAILURE #{consecutive_failures}] Text without tool call"
                    )

                # Detect code-style tool calls (Gemini tool_code pattern)
                code_style_match = re.search(
                    r"finish_task\s*\(\s*answer\s*=\s*['\"](.+?)['\"]\s*(?:,\s*sources\s*=\s*(\[.+?\]))?\s*\)",
                    final_content,
                    re.DOTALL,
                )
                if code_style_match and browse_web_succeeded:
                    parsed_answer = code_style_match.group(1)
                    print(
                        f"🔄 [AUTO-RECOVERY] Parsed finish_task from code-style text output"
                    )
                    new_messages.append(assistant_msg)
                    return {
                        "response": parsed_answer,
                        "tool_used": "finish_task (auto-recovered)",
                        "tool_result": None,
                        "raw_url": last_raw_url,
                        "new_messages": new_messages,
                    }

                # Hard bail-out: after 2 consecutive failures, auto-finish
                # if we already have browsed data
                if consecutive_failures >= 2 and browse_web_succeeded:
                    # Try to build a useful answer from the last tool result
                    fallback_answer = final_content.strip() if final_content.strip() else None

                    if not fallback_answer and last_tool_result and isinstance(last_tool_result, dict):
                        # Extract data from the last successful tool result
                        data = last_tool_result.get("data") or last_tool_result.get("content")
                        if isinstance(data, dict):
                            parts = [f"{k}: {v}" for k, v in data.items() if v is not None]
                            fallback_answer = "; ".join(parts) if parts else None
                        elif isinstance(data, str) and len(data) > 20:
                            fallback_answer = data[:500]

                    if not fallback_answer:
                        fallback_answer = (
                            "I was able to browse the relevant webpage but encountered "
                            "difficulties extracting a structured answer. Please try again "
                            "or refine the query."
                        )

                    print(
                        f"🔄 [AUTO-RECOVERY] Bailing out after {consecutive_failures} failures. Using available data."
                    )
                    new_messages.append(assistant_msg)
                    return {
                        "response": fallback_answer,
                        "tool_used": "auto-recovery",
                        "tool_result": last_tool_result,
                        "raw_url": last_raw_url,
                        "new_messages": new_messages,
                    }

                # Inject context-aware nudge
                if browse_web_succeeded and not extract_data_called:
                    nudge = (
                        "[SYSTEM CORRECTION] You output text without calling a tool. "
                        "You have already browsed a page. If you have the information you need, call finish_task NOW. "
                        "If you still need specific facts from the page, call extract_data(fields). "
                        "Use the function calling interface — do NOT write code blocks."
                        + task_anchor
                    )
                elif search_web_called and not browse_web_succeeded:
                    nudge = (
                        "[SYSTEM CORRECTION] You have search results. "
                        "Call browse_web on a specific URL from those results NOW."
                        + task_anchor
                    )
                else:
                    nudge = (
                        "[SYSTEM CORRECTION] You must call search_web or browse_web to gather data. "
                        "Call a tool NOW."
                        + task_anchor
                    )

                nudge_msg = {"role": "user", "content": nudge}
                full_messages += [assistant_msg, nudge_msg]
                new_messages += [assistant_msg, nudge_msg]
                continue

            # ── Process tool calls ────────────────────────────────────
            consecutive_failures = 0  # reset on successful tool call

            print(
                f"🛠️  [TOOLS] {len(message.tool_calls)} tool call(s)"
            )
            assistant_msg = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [],
            }

            tool_msgs = []
            tools_in_step = []
            finished = False
            final_answer = ""

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                # Compact log of tool execution
                log_args = {
                    k: (v[:100] + "…" if isinstance(v, str) and len(v) > 100 else v)
                    for k, v in tool_args.items()
                }
                print(f"   👉 {tool_name}({log_args})")

                tool_result = await execute_tool(tool_name, tool_args)
                is_success = (
                    tool_result.get("success", False)
                    if isinstance(tool_result, dict)
                    else True
                )
                print(
                    f"   {'✅' if is_success else '❌'} {tool_name} → {'ok' if is_success else 'FAILED'}"
                )

                # Update tracking flags
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
                    final_answer = tool_args.get("answer", "") or tool_result.get(
                        "answer", ""
                    )
                elif tool_name == "observe_page" and is_success:
                    last_observe_result = (
                        tool_result if isinstance(tool_result, dict) else {}
                    )
                elif tool_name in ("click_element", "fill_form_field") and is_success:
                    pass  # form panel state updated below

                if isinstance(tool_result, dict):
                    fps = tool_result.get("form_panel_state") or {}
                    if fps.get("panel_open") or tool_result.get(
                        "skipped_redundant_click"
                    ):
                        form_panel_open = True
                    elif tool_name == "click_element" and is_success and _is_submit_click_intent(
                        tool_args
                    ):
                        form_panel_open = False

                tools_in_step.append(tool_name)
                last_tool_used = ", ".join(tools_in_step)
                last_tool_result = tool_result
                last_raw_url = (
                    tool_args.get("url")
                    if isinstance(tool_args, dict)
                    else last_raw_url
                )

                assistant_msg["tool_calls"].append(
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args),
                        },
                    }
                )

                # Truncate tool result before adding to conversation
                tool_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": _truncate_tool_result_for_conversation(
                            tool_name, tool_result
                        ),
                    }
                )

            new_messages.append(assistant_msg)
            full_messages.append(assistant_msg)

            for tool_msg in tool_msgs:
                new_messages.append(tool_msg)
                full_messages.append(tool_msg)

            if finished:
                print(f"\n{'=' * 50}")
                print(f"✅ TASK COMPLETED")
                print(f"🏁 {final_answer[:300]}{'…' if len(final_answer) > 300 else ''}")
                print(f"{'=' * 50}\n")
                return {
                    "response": final_answer,
                    "tool_used": last_tool_used,
                    "tool_result": last_tool_result,
                    "raw_url": last_raw_url,
                    "new_messages": new_messages,
                }

            # ── Observe-only loop detection ─────────────────────────────
            step_tool_names = {tc.function.name for tc in message.tool_calls}
            progress_tools = {"click_element", "fill_form_field", "navigate_page", "browse_web", "scroll"}
            if step_tool_names == {"observe_page"}:
                consecutive_observe_only += 1
            elif step_tool_names & progress_tools:
                consecutive_observe_only = 0

            if consecutive_observe_only >= 1 and last_observe_result:
                # Prefer live form panel state over stale observe next_action
                live_panel: Dict[str, Any] = {}
                if browser_manager.current_page and not browser_manager.current_page.is_closed():
                    try:
                        live_panel = await detect_form_panel_state(
                            browser_manager.current_page
                        )
                    except Exception:
                        pass
                panel = live_panel if live_panel.get("panel_open") else (
                    last_observe_result.get("form_panel_state") or {}
                )

                if panel.get("panel_open") or form_panel_open:
                    rec_tool = panel.get("recommended_next") or "fill_form_field"
                    rec_intent = panel.get("recommended_intent") or "description"
                    save_btn = (panel.get("submit_buttons") or ["Save changes"])[0]
                    if rec_tool == "fill_form_field":
                        action_line = (
                            f"Edit panel is OPEN. Call fill_form_field(field_description=\"{rec_intent}\", "
                            f"value=<your description text>). Do NOT call observe_page or click edit again."
                        )
                    else:
                        action_line = (
                            f"Edit panel is OPEN and fields are filled. "
                            f"Call click_element(intent=\"{save_btn}\"). "
                            f"Do NOT call observe_page or click edit again."
                        )
                else:
                    next_action = last_observe_result.get("next_action") or {}
                    next_tool = next_action.get("tool") or "click_element"
                    next_intent = next_action.get("intent") or "the element identified in vision_guidance"
                    vision_hint = (last_observe_result.get("vision_guidance") or "")[:400]
                    if next_tool == "click_element":
                        action_line = f'IMMEDIATELY call click_element with intent "{next_intent}".'
                    elif next_tool == "scroll":
                        direction = "top"
                        if next_intent and "down" in next_intent.lower():
                            direction = "down"
                        elif next_intent and "up" in next_intent.lower():
                            direction = "up"
                        action_line = f"IMMEDIATELY call scroll(direction='{direction}')."
                    elif next_tool == "fill_form_field":
                        action_line = f'IMMEDIATELY call fill_form_field for "{next_intent}".'
                    elif next_tool == "browse_web" and next_action.get("url"):
                        action_line = f"IMMEDIATELY call browse_web(url='{next_action['url']}')."
                    else:
                        action_line = f"IMMEDIATELY call {next_tool} to make progress."
                    vision_hint = (last_observe_result.get("vision_guidance") or "")[:400]
                    act_nudge = (
                        "[SYSTEM — ACT NOW] You called observe_page and received a plan. "
                        "Do NOT call observe_page again without acting first.\n"
                        f"{action_line}\n"
                        + (f"Vision guidance: {vision_hint}\n" if vision_hint else "")
                        + task_anchor
                    )
                    if consecutive_observe_only >= 1:
                        print(f"🎯 [OBSERVE→ACT] Injecting action nudge after observe_page")
                        act_msg = {"role": "user", "content": act_nudge}
                        full_messages.append(act_msg)
                        new_messages.append(act_msg)
                    continue

                act_nudge = (
                    "[SYSTEM — PANEL OPEN] An edit form/dialog is already visible on screen. "
                    "Do NOT call observe_page or click the edit trigger again.\n"
                    f"{action_line}"
                    + task_anchor
                )
                if consecutive_observe_only >= 1:
                    print(f"🎯 [OBSERVE→ACT] Injecting action nudge after observe_page")
                    act_msg = {"role": "user", "content": act_nudge}
                    full_messages.append(act_msg)
                    new_messages.append(act_msg)

            # ── Stuck detection: consecutive action-tool failures ──────
            action_tools = {"click_element", "fill_form_field", "navigate_page"}
            step_had_action_failure = False
            for tc in message.tool_calls:
                tn = tc.function.name
                if tn in action_tools:
                    # Check if this specific tool failed
                    tc_result = next(
                        (tm for tm in tool_msgs if tm.get("tool_call_id") == tc.id),
                        None
                    )
                    if tc_result:
                        try:
                            res_data = json.loads(tc_result["content"])
                            if isinstance(res_data, dict) and not res_data.get("success", True):
                                step_had_action_failure = True
                        except Exception:
                            pass

            # Track action signature to detect repeats
            action_sig = "|".join(
                f"{tc.function.name}:{tc.function.arguments}"
                for tc in message.tool_calls
            )
            if action_sig == last_action_signature:
                repeat_count += 1
            else:
                repeat_count = 0
            last_action_signature = action_sig

            if step_had_action_failure:
                consecutive_tool_failures += 1
            else:
                consecutive_tool_failures = 0

            # Inject recovery nudge when stuck
            if consecutive_tool_failures >= 2 or repeat_count >= 2:
                stuck_nudge = (
                    "[SYSTEM — STUCK DETECTED] You have failed the same type of action "
                    f"{consecutive_tool_failures} time(s) in a row"
                    + (f" and repeated the same action {repeat_count + 1} times" if repeat_count >= 2 else "")
                    + ". You are STUCK. You MUST change strategy NOW:\n"
                    "1. Call observe_page to see what is ACTUALLY on screen right now.\n"
                    "2. Check if you are on the WRONG PAGE. If so, call go_back() to return to the previous page.\n"
                    "3. Try a completely different approach — different click intent wording, "
                    "a direct URL via browse_web, or scroll to find hidden elements.\n"
                    "4. If nothing works after 3+ different strategies, take_screenshot to visually inspect the page.\n"
                    "Do NOT repeat the same failing action. Do NOT call finish_task yet."
                    + task_anchor
                )
                print(f"🔄 [STUCK DETECTED] Injecting recovery nudge (failures={consecutive_tool_failures}, repeats={repeat_count})")
                stuck_msg = {"role": "user", "content": stuck_nudge}
                full_messages.append(stuck_msg)
                new_messages.append(stuck_msg)

        # ── Max steps exceeded ────────────────────────────────────────
        print(f"⚠️  [MAX STEPS] Exceeded {max_steps} steps.")
        fallback_content = (
            "I have reached the maximum number of browsing steps to complete this request."
        )
        new_messages.append({"role": "assistant", "content": fallback_content})
        return {
            "response": fallback_content,
            "tool_used": last_tool_used,
            "tool_result": last_tool_result,
            "raw_url": last_raw_url,
            "new_messages": new_messages,
        }


agent = AIAgent()
