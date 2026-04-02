from __future__ import annotations

import json

from rocky.config.models import ProviderConfig
from rocky.core.messages import Message
from rocky.providers.base import sanitize_assistant_text
from rocky.providers.openai_chat import OpenAIChatProvider


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("POST", "http://example.test/chat/completions")
            response = httpx.Response(self.status_code, request=request, json=self._payload)
            raise httpx.HTTPStatusError("boom", request=request, response=response)
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[dict | tuple[int, dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append({"url": url, "json": json})
        item = self.responses.pop(0)
        if isinstance(item, tuple):
            status_code, payload = item
            return _FakeResponse(payload, status_code=status_code)
        return _FakeResponse(item)


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


def test_run_with_tools_retries_forced_final_when_first_reply_is_empty() -> None:
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
                                        "name": "run_python",
                                        "arguments": json.dumps({"code": "print(1)"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "choices": [{"message": {"content": ""}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
            {
                "choices": [{"message": {"content": "{\"ok\": true}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            },
        ]
    )
    provider._client = lambda: fake_client  # type: ignore[method-assign]

    response = provider.run_with_tools(
        system_prompt="You are Rocky.",
        messages=[Message(role="user", content="extract json")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "run_python",
                    "description": "Run python",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        execute_tool=lambda name, arguments: json.dumps({"success": True, "data": {"stdout": "{\"ok\": true}"}}),
        max_rounds=1,
    )

    assert response.text == "{\"ok\": true}"
    assert response.raw["forced_final"] is True
    assert len(fake_client.calls) == 3
    assert fake_client.calls[1]["json"]["temperature"] == 0
    assert fake_client.calls[2]["json"]["temperature"] == 0


def test_run_with_tools_falls_back_to_tool_summary_when_forced_final_stays_empty() -> None:
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
                                        "name": "write_file",
                                        "arguments": json.dumps({"path": "report.sh", "content": "echo ok"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            {
                "choices": [{"message": {"content": ""}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            },
            {
                "choices": [{"message": {"content": ""}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            },
        ]
    )
    provider._client = lambda: fake_client  # type: ignore[method-assign]

    response = provider.run_with_tools(
        system_prompt="You are Rocky.",
        messages=[Message(role="user", content="create and verify the script")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        execute_tool=lambda name, arguments: json.dumps(
            {
                "success": True,
                "data": {"path": "report.sh", "command": "sh report.sh", "stdout": "ok\n"},
            }
        ),
        max_rounds=1,
    )

    assert "Completed the requested file changes: `report.sh`." in response.text
    assert response.raw["forced_final"] is True


def test_run_with_tools_strips_internal_tool_citation_markers() -> None:
    provider = OpenAIChatProvider(ProviderConfig(name="ollama"))
    fake_client = _FakeClient(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "Implemented in `src/rocky/tools/shell.py`【grep_files†data[0].line】",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ]
    )
    provider._client = lambda: fake_client  # type: ignore[method-assign]

    response = provider.run_with_tools(
        system_prompt="You are Rocky.",
        messages=[Message(role="user", content="where is this implemented?")],
        tools=[],
        execute_tool=lambda name, arguments: "",
        max_rounds=1,
    )

    assert response.text == "Implemented in `src/rocky/tools/shell.py`"


def test_sanitize_assistant_text_can_preserve_stream_chunk_spacing() -> None:
    assert sanitize_assistant_text(" Hello ", strip=False) == " Hello "


def test_run_with_tools_retries_transient_server_errors() -> None:
    provider = OpenAIChatProvider(ProviderConfig(name="ollama"))
    fake_client = _FakeClient(
        [
            (500, {"error": {"message": "temporary"}}),
            {
                "choices": [
                    {
                        "message": {
                            "content": "Recovered answer.",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
    )
    provider._client = lambda: fake_client  # type: ignore[method-assign]

    response = provider.run_with_tools(
        system_prompt="You are Rocky.",
        messages=[Message(role="user", content="recover please")],
        tools=[],
        execute_tool=lambda name, arguments: "",
        max_rounds=1,
    )

    assert response.text == "Recovered answer."
    assert len(fake_client.calls) == 2
