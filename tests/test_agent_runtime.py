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
                        "name": "read_file",
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
                    "name": "read_file",
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


class _AutomationShellWriteGuardProvider:
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        blocked = execute_tool(
            "run_shell_command",
            {"command": "cat > scripts/report.sh <<'EOF'\necho hi\nEOF", "timeout_s": 5},
        )
        wrote = execute_tool(
            "write_file",
            {"path": "scripts/report.sh", "content": "#!/bin/sh\necho hi\n"},
        )
        reread = execute_tool("read_file", {"path": "scripts/report.sh"})
        verified = execute_tool("run_shell_command", {"command": "sh scripts/report.sh", "timeout_s": 5})
        return ProviderResponse(
            text="Ran `sh scripts/report.sh` and it printed `hi`.",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "cat > scripts/report.sh <<'EOF'\necho hi\nEOF", "timeout_s": 5},
                    "text": blocked,
                    "success": False,
                },
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "scripts/report.sh", "content": "#!/bin/sh\necho hi\n"},
                    "text": wrote,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "scripts/report.sh"},
                    "text": reread,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "sh scripts/report.sh", "timeout_s": 5},
                    "text": verified,
                    "success": True,
                },
            ],
        )


class _AutomationPreWriteShellLoopProvider:
    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        inspect = execute_tool("run_shell_command", {"command": "ls", "timeout_s": 5})
        blocked = execute_tool("run_shell_command", {"command": "mkdir -p scripts backups", "timeout_s": 5})
        wrote = execute_tool(
            "write_file",
            {"path": "scripts/backup_logs.sh", "content": "#!/bin/sh\nmkdir -p backups\ncp logs/app.log backups/app.log\n"},
        )
        reread = execute_tool("read_file", {"path": "scripts/backup_logs.sh"})
        verified = execute_tool(
            "run_shell_command",
            {"command": "sh scripts/backup_logs.sh && test -f backups/app.log", "timeout_s": 5},
        )
        return ProviderResponse(
            text="Ran `sh scripts/backup_logs.sh` and verified backups/app.log exists.",
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "ls", "timeout_s": 5},
                    "text": inspect,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "mkdir -p scripts backups", "timeout_s": 5},
                    "text": blocked,
                    "success": False,
                },
                {
                    "type": "tool_result",
                    "name": "write_file",
                    "arguments": {"path": "scripts/backup_logs.sh"},
                    "text": wrote,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "read_file",
                    "arguments": {"path": "scripts/backup_logs.sh"},
                    "text": reread,
                    "success": True,
                },
                {
                    "type": "tool_result",
                    "name": "run_shell_command",
                    "arguments": {"command": "sh scripts/backup_logs.sh && test -f backups/app.log", "timeout_s": 5},
                    "text": verified,
                    "success": True,
                },
            ],
        )


class _FailingProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        raise RuntimeError("provider offline")


class _LearningFailingProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        return ProviderResponse(text="ok")


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


def test_freeze_load_does_not_create_internal_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path, freeze=True)

    assert runtime.freeze_enabled is True
    assert not runtime.workspace.rocky_dir.exists()
    assert not runtime.global_root.exists()


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


def test_runtime_ignores_learning_record_failures(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    registry = _ProviderRegistry(_LearningFailingProvider())
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    def _boom(*args, **kwargs):
        raise RuntimeError("learning write failed")

    runtime.learning_manager.record_query = _boom  # type: ignore[assignment]
    runtime.agent.learning_manager = runtime.learning_manager

    response = runtime.run_prompt("hello")

    assert response.text == "ok"
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


def test_freeze_continue_session_reads_existing_messages_without_persisting(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    current = runtime.sessions.ensure_current()
    current.append("user", "old task")
    current.append("assistant", "old answer")
    runtime.sessions.save(current)

    frozen = RockyRuntime.load_from(tmp_path, freeze=True)
    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    frozen.provider_registry = registry
    frozen.agent.provider_registry = registry

    sessions_before = list(runtime.workspace.sessions_dir.glob("ses_*.json"))
    traces_before = list(runtime.workspace.traces_dir.glob("trace_*.json"))
    query_before = list(runtime.workspace.episodes_query_dir.glob("qry_*.json"))

    response = frozen.run_prompt("new task", continue_session=True)

    assert response.text == "ok"
    assert [message.content for message in provider.calls[0]] == ["old task", "old answer", "new task"]
    reloaded = runtime.sessions.load(current.id)
    assert [message["content"] for message in reloaded.messages[-2:]] == ["old task", "old answer"]
    assert list(runtime.workspace.sessions_dir.glob("ses_*.json")) == sessions_before
    assert list(runtime.workspace.traces_dir.glob("trace_*.json")) == traces_before
    assert list(runtime.workspace.episodes_query_dir.glob("qry_*.json")) == query_before
    assert "trace_path" not in response.trace


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


def test_freeze_run_does_not_recreate_internal_state_dirs(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path, freeze=True)
    provider = _OkProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("hello", continue_session=False)

    assert response.text == "ok"
    assert not runtime.workspace.sessions_dir.exists()
    assert not runtime.workspace.traces_dir.exists()
    assert not runtime.workspace.episodes_query_dir.exists()
    assert "trace_path" not in response.trace


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


def test_runtime_blocks_shell_file_creation_before_write_file_for_automation(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _AutomationShellWriteGuardProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("build a repeatable report script and run it", continue_session=False)

    assert response.verification["status"] == "pass"
    first_event = response.trace["tool_events"][0]
    assert first_event["name"] == "run_shell_command"
    assert first_event["success"] is False
    assert "use_write_file_first" in first_event["text"]
    assert response.trace["tool_events"][1]["name"] == "write_file"


def test_runtime_allows_only_one_lightweight_shell_inspection_before_write_file(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    provider = _AutomationPreWriteShellLoopProvider()
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("automate a backup log script and verify it runs", continue_session=False)

    assert response.verification["status"] == "pass"
    assert response.trace["tool_events"][0]["name"] == "run_shell_command"
    assert response.trace["tool_events"][0]["success"] is True
    assert response.trace["tool_events"][1]["name"] == "run_shell_command"
    assert response.trace["tool_events"][1]["success"] is False
    assert "use_write_file_first" in response.trace["tool_events"][1]["text"]
    assert response.trace["tool_events"][2]["name"] == "write_file"
