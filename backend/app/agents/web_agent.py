import json
import logging
from typing import Any, Dict, List

from app.agents.prompts import WEB_AGENT_SYSTEM_PROMPT
from app.services.llm_service import get_openai_client
from app.config import settings
from app.tools.tool_registry import execute_tool, get_tool_definitions

logger = logging.getLogger(__name__)


class AIAgent:
    def __init__(self):
        self.model = settings.openrouter_model
        self.tools = get_tool_definitions()

    async def chat(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Main chat loop. Handles tool calling automatically."""
        logger.info(f"Starting chat session with {len(messages)} input messages")
        client = get_openai_client()
        full_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": WEB_AGENT_SYSTEM_PROMPT},
            *messages,
        ]

        new_messages = []
        max_steps = 10
        step = 0

        last_tool_used = None
        last_tool_result = None
        last_raw_url = None

        while step < max_steps:
            step += 1
            logger.info(f"Agent step {step}/{max_steps} - Requesting LLM completion")
            response = await client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=self.tools,
                tool_choice="auto" if step < max_steps else "none",
            )

            message = response.choices[0].message

            if not message.tool_calls:
                final_content = message.content or ""
                logger.info(f"LLM decided to respond directly without tool calls. Content length: {len(final_content)}")
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
            assistant_msg = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": []
            }

            tool_msgs = []

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                logger.info(f"Executing tool '{tool_name}' with args: {tool_args}")
                tool_result = await execute_tool(tool_name, tool_args)
                is_success = tool_result.get("success", False) if isinstance(tool_result, dict) else True
                logger.info(f"Tool '{tool_name}' execution completed. Success: {is_success}")

                last_tool_used = tool_name
                last_tool_result = tool_result
                last_raw_url = tool_args.get("url") if isinstance(tool_args, dict) else None

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
