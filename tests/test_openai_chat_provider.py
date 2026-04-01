from __future__ import annotations

import json

from rocky.config.models import ProviderConfig
from rocky.core.messages import Message
from rocky.providers.openai_chat import OpenAIChatProvider


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append({"url": url, "json": json})
        return _FakeResponse(self.responses.pop(0))


def test_run_with_tools_forces_final_answer_after_tool_loop() -> None:
    provider = OpenAIChatProvider(ProviderConfig(name="ollama"))
    fake_client = _FakeClient(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "run_shell_command",
                                        "arguments": json.dumps({"command": "whoami"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "Final answer from forced completion.",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
        ]
    )
    provider._client = lambda: fake_client  # type: ignore[method-assign]

    response = provider.run_with_tools(
        system_prompt="You are Rocky.",
        messages=[Message(role="user", content="who am i?")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "run_shell_command",
                    "description": "Run shell",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        execute_tool=lambda name, arguments: json.dumps({"success": True, "data": {"stdout": "flrngel"}}),
        max_rounds=1,
    )

    assert response.text == "Final answer from forced completion."
    assert response.raw["forced_final"] is True
    assert "tools" in fake_client.calls[0]["json"]
    assert "tools" not in fake_client.calls[1]["json"]
