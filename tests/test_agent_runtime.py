from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.providers.base import ProviderResponse


class _OkProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
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
    registry = _ProviderRegistry(_OkProvider())
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("hello")

    assert response.text == "ok"
    assert response.trace["selected_tools"] == []


def test_runtime_returns_failure_response_when_provider_errors(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    registry = _ProviderRegistry(_FailingProvider())
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt("hello")

    assert response.verification["status"] == "fail"
    assert "provider offline" in response.text
    assert response.trace["error"]["type"] == "RuntimeError"
