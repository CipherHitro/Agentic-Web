import json
from typing import Any, Dict, List

from app.agents.prompts import WEB_AGENT_SYSTEM_PROMPT
from app.services.llm_service import get_openai_client
from app.config import settings
from app.tools.tool_registry import execute_tool, get_tool_definitions


class AIAgent:
    def __init__(self):
        self.model = settings.openrouter_model
        self.tools = get_tool_definitions()

    async def chat(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Main chat loop. Handles tool calling automatically."""
        client = get_openai_client()
        full_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": WEB_AGENT_SYSTEM_PROMPT},
            *messages,
        ]

        response = await client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            tools=self.tools,
            tool_choice="auto",
        )

        message = response.choices[0].message

        if message.tool_calls:
            tool_call = message.tool_calls[0]
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)

            tool_result = await execute_tool(tool_name, tool_args)

            assistant_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args),
                        },
                    }
                ],
            }
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": tool_name,
                "content": json.dumps(tool_result),
            }

            full_messages.append(assistant_msg)
            full_messages.append(tool_msg)

            final_response = await client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=self.tools,
            )

            final_content = final_response.choices[0].message.content or ""
            return {
                "response": final_content,
                "tool_used": tool_name,
                "tool_result": tool_result,
                "raw_url": tool_args.get("url"),
                "new_messages": [
                    assistant_msg,
                    tool_msg,
                    {
                        "role": "assistant",
                        "content": final_content,
                    }
                ]
            }

        final_content = message.content or ""
        return {
            "response": final_content,
            "tool_used": None,
            "tool_result": None,
            "new_messages": [
                {
                    "role": "assistant",
                    "content": final_content,
                }
            ]
        }


agent = AIAgent()
