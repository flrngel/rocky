from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.core.messages import Message
from rocky.providers.base import ProviderResponse


class _OkProvider:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls.append(messages)
        return ProviderResponse(text="ok")


class _FailingProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        raise RuntimeError("provider offline")


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
