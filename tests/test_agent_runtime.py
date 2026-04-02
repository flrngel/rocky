from __future__ import annotations

import os
from pathlib import Path
import shutil

import httpx

from rocky.app import RockyRuntime
from rocky.core.messages import Message
from rocky.providers.base import ProviderResponse


class _OkProvider:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []
        self.tool_calls: list[dict] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls.append(messages)
        return ProviderResponse(text="ok")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        return ProviderResponse(
            text="runtime inspected",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "inspect_runtime_versions",
                    "arguments": {"targets": ["python"]},
                    "text": "{}",
                    "success": True,
                }
            ],
        )


class _ExtractionProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        return ProviderResponse(
            text='Done.\n```json\n{"rows": 2, "fields": ["name"]}\n```',
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "data/people.jsonl"},
                    "text": "{}",
                    "success": True,
                }
            ],
        )


class _RepairingExtractionProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.complete_calls: list[list[Message]] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        return ProviderResponse(
            text="I found 2 rows and the fields are name and role.",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_python",
                    "arguments": {"code": "print(...)"},
                    "text": '{"success": true, "data": {"stdout": "{\\"rows\\": 2, \\"fields\\": [\\"name\\", \\"role\\"]}"}}',
                    "success": True,
                }
            ],
        )

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(messages)
        return ProviderResponse(text='{"rows": 2, "fields": ["name", "role"]}')


class _HiddenToolProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        hidden = execute_tool("write_file", {"path": "oops.txt", "content": "nope"})
        return ProviderResponse(
            text="checked hidden tool",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "oops.txt", "content": "nope"},
                    "text": hidden,
                    "success": False,
                }
            ],
        )


class _RepairingToolExpectationProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        if len(self.tool_calls) == 1:
            return ProviderResponse(
                text="Created note.txt.",
                raw={"rounds": ["initial"]},
                tool_events=[
                    {
                        "type": "tool_result",
                        "name": "run_shell_command",
                        "arguments": {"command": "printf 'hello\\n' > note.txt"},
                        "text": "{}",
                        "success": True,
                    }
                ],
            )
        return ProviderResponse(
            text="Created note.txt, read it, and confirmed the file exists.",
            raw={"rounds": ["retry"]},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "printf 'hello\\n' > note.txt"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "note.txt"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "stat_path",
                    "arguments": {"path": "note.txt"},
                    "text": "{}",
                    "success": True,
                },
            ],
        )


class _AutomationOutputRepairProvider:
    def __init__(self) -> None:
        self.tool_calls: list[dict] = []
        self.complete_calls: list[list[Message]] = []

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append({"messages": messages, "tools": tools, "max_rounds": max_rounds})
        if len(self.tool_calls) == 1:
            return ProviderResponse(
                text="Done. Created the files and `sh report.sh` printed `220`.",
                raw={"rounds": ["initial"]},
                tool_events=[
                    {
                        "type": "tool_result",
                        "name": "write_file",
                        "arguments": {"path": "sales.csv"},
                        "text": "{}",
                        "success": True,
                    },
                    {
                        "type": "tool_result",
                        "name": "write_file",
                        "arguments": {"path": "report.sh"},
                        "text": "{}",
                        "success": True,
                    },
                    {
                        "type": "tool_result",
                        "name": "run_shell_command",
                        "arguments": {"command": "sh report.sh"},
                        "text": '{"success": true, "data": {"stdout": "220\\n", "stderr": "", "returncode": 0}}',
                        "success": True,
                    },
                ],
            )
        return ProviderResponse(
            text="Done. Created the files and `sh report.sh` printed `360`.",
            raw={"rounds": ["retry"]},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "report.sh"},
                    "text": "{}",
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "sh report.sh"},
                    "text": '{"success": true, "data": {"stdout": "360\\n", "stderr": "", "returncode": 0}}',
                    "success": True,
                },
            ],
        )

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(messages)
        if len(self.complete_calls) == 1:
            return ProviderResponse(
                text='{"status":"fail","reason":"The observed total should be 360, not 220."}'
            )
        return ProviderResponse(
            text='{"status":"pass","reason":"The observed total matches the requested output."}'
        )


class _FailingProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        raise RuntimeError("provider offline")


class _FlakyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            request = httpx.Request("POST", "http://example.test/chat/completions")
            response = httpx.Response(500, request=request, json={"error": "temporary"})
            raise httpx.HTTPStatusError("temporary", request=request, response=response)
        return ProviderResponse(text="ok after retry")


class _ProviderRegistry:
    def __init__(self, provider) -> None:
        self.provider = provider

    def provider_for_task(self, needs_tools: bool = False):
        return self.provider


def test_runtime_trace_uses_no_tools_for_direct_prompt(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("hello")

    assert response.text == "ok"
    assert response.trace["selected_tools"] == []
    assert [message.content for message in provider.calls[0]] == ["hello"]


def test_runtime_returns_failure_response_when_provider_errors(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    registry = _ProviderRegistry(_FailingProvider())
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("hello")

    assert response.verification["status"] == "fail"
    assert "provider offline" in response.text
    assert response.trace["error"]["type"] == "RuntimeError"


def test_runtime_retries_transient_provider_failures(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    provider = _FlakyProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("hello")

    assert response.text == "ok after retry"
    assert provider.calls == 2
    assert response.verification["status"] == "pass"


def test_isolated_run_does_not_include_previous_session_messages(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("new task", continue_session=False)

    assert response.text == "ok"
    assert [message.content for message in provider.calls[0]] == ["new task"]
    assert runtime.sessions.ensure_current().id == current.id
    assert runtime.sessions.ensure_current().messages[-1]["content"] == "old answer"


def test_session_run_can_still_include_previous_messages(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    runtime.run_prompt("new task", continue_session=True)

    assert [message.content for message in provider.calls[0]] == ["old task", "old answer", "new task"]


def test_isolated_run_refuses_to_invent_previous_turns(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("what was my previous question?", continue_session=False)

    assert "don't have any earlier turn context" in response.text
    assert provider.calls == []


def test_runtime_inspection_prompt_uses_tool_capable_provider(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python3 = bin_dir / "python3"
    python3.write_text("#!/bin/sh\necho Python 3.14.3\n", encoding="utf-8")
    python3.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("what python versions do i have", continue_session=False)

    assert response.text == "runtime inspected"
    assert response.trace["provider"] == "_OkProvider"
    assert provider.calls == []
    assert len(provider.tool_calls) == 1
    tool_names = {tool["function"]["name"] for tool in provider.tool_calls[0]["tools"]}
    assert "inspect_runtime_versions" in tool_names


def test_extraction_route_normalizes_json_fence_output(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ExtractionProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("normalize the people dataset into json", continue_session=False)

    assert response.text == '{"rows": 2, "fields": ["name"]}'
    assert response.verification["status"] == "pass"


def test_extraction_route_repairs_prose_into_json_with_provider(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _RepairingExtractionProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("normalize the people dataset into json", continue_session=False)

    assert response.text == '{"rows": 2, "fields": ["name", "role"]}'
    assert response.verification["status"] == "pass"
    assert len(provider.tool_calls) == 2
    assert len(provider.complete_calls) == 2


def test_automation_route_gets_more_tool_rounds(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    runtime.run_prompt("create a repeatable cleanup script and verify it", continue_session=False)

    assert provider.tool_calls[0]["max_rounds"] == 12


def test_extraction_route_gets_extended_tool_rounds(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _ExtractionProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    runtime.run_prompt("normalize the people dataset into json", continue_session=False)

    assert provider.tool_calls[0]["max_rounds"] == 8


def test_runtime_refuses_unexposed_tools_from_provider(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _HiddenToolProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("what python versions do i have", continue_session=False)

    assert response.verification["status"] == "fail"
    tool_result = response.trace["tool_events"][0]
    assert tool_result["name"] == "write_file"
    assert "\"tool_not_exposed\"" in tool_result["text"]


def test_runtime_recreates_internal_state_dirs_if_deleted_mid_run(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    shutil.rmtree(runtime.sessions.sessions_dir)
    shutil.rmtree(runtime.agent.traces_dir)

    response = runtime.run_prompt("hello", continue_session=False)

    assert response.text == "ok"
    assert runtime.sessions.sessions_dir.exists()
    assert runtime.agent.traces_dir.exists()
    assert Path(response.trace["trace_path"]).exists()


def test_runtime_retries_tool_loop_after_verification_failure(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _RepairingToolExpectationProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "run a command that creates note.txt, then read it and stat it",
        continue_session=False,
    )

    assert response.verification["status"] == "pass"
    assert len(provider.tool_calls) == 2
    retried_messages = provider.tool_calls[1]["messages"]
    assert retried_messages[-1].role == "user"
    assert "did not pass verification" in str(retried_messages[-1].content)


def test_runtime_retries_automation_when_judged_output_is_wrong(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _AutomationOutputRepairProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(
        "Build a tiny shell script project in this empty workspace. "
        "Create exactly these files: sales.csv, report.sh, and README.md. "
        "Then run sh report.sh to verify it works and tell me the exact output.",
        continue_session=False,
    )

    assert response.verification["status"] == "pass"
    assert len(provider.tool_calls) == 2
    assert len(provider.complete_calls) == 2
    retried_messages = provider.tool_calls[1]["messages"]
    assert retried_messages[-1].role == "user"
    assert "360" in str(retried_messages[-1].content)
