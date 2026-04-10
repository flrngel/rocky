from __future__ import annotations

import json

from rocky.config.models import ProviderConfig, ProviderStyle
from rocky.core.messages import Message
from rocky.providers.litellm_chat import LiteLLMChatProvider


class _FakeLiteLLM:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)



def test_litellm_run_with_tools_normalizes_dict_arguments_and_function_call() -> None:
    provider = LiteLLMChatProvider(
        ProviderConfig(
            name="litellm_local",
            style=ProviderStyle.LITELLM_CHAT,
            base_url="http://localhost:4000",
            model="ollama_chat/qwen3.5:4b",
        )
    )
    fake_litellm = _FakeLiteLLM(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "function_call": {
                                "name": "read_file",
                                "arguments": {"path": "README.md"},
                            },
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "Done.",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
        ]
    )
    provider._litellm = lambda: fake_litellm  # type: ignore[method-assign]

    captured_arguments: list[dict] = []
    response = provider.run_with_tools(
        system_prompt="You are Rocky.",
        messages=[Message(role="user", content="read the readme")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        execute_tool=lambda name, arguments: captured_arguments.append(arguments) or json.dumps({"success": True, "data": {"path": "README.md"}}),
        max_rounds=1,
    )

    assert response.text == "Done."
    assert captured_arguments == [{"path": "README.md"}]
    replay_messages = fake_litellm.calls[1]["messages"]
    assistant_message = next(message for message in replay_messages if message.get("role") == "assistant" and message.get("tool_calls"))
    tool_message = next(message for message in replay_messages if message.get("role") == "tool")
    assert assistant_message["content"] is None
    assert assistant_message["tool_calls"][0]["id"] == "call_1"
    assert tool_message["tool_call_id"] == "call_1"
    assert tool_message["content"] == response.tool_events[-1]["model_text"]
    assert not tool_message["content"].lstrip().startswith("{")
