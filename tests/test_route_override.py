Status: DONE
"""O2 — CLI/API route override (C1-override).

Callers without --route see bit-identical behavior (CF-4).
With --route research/live_compare/general the lexical router is bypassed and
the specified signature is used.  The teach-guard
(_maybe_upgrade_route_from_project_context) still runs unconditionally after
the override so legitimate policy upgrades are preserved.
"""

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.core.router import TaskClass
from rocky.providers.base import ProviderResponse


# ---------------------------------------------------------------------------
# Shared stub provider
# ---------------------------------------------------------------------------

class _OkProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        return ProviderResponse(text="research answer")

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        return ProviderResponse(text="research answer", raw={"rounds": []}, tool_events=[])


class _ProviderRegistry:
    def __init__(self, provider) -> None:
        self.provider = provider

    def provider_for_task(self, needs_tools: bool = False):
        return self.provider


def _set_provider(runtime: RockyRuntime, provider) -> None:
    registry = _ProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry


# ---------------------------------------------------------------------------
# Test 1 — unit: agent respects route_override
# ---------------------------------------------------------------------------

def test_route_override_bypasses_lexical_classification(tmp_path: Path, monkeypatch) -> None:
    """Prompt 'run ls' would normally classify as repo/shell_execution.

    With route_override='research/live_compare/general' the resulting route
    must carry that signature regardless of the prompt text.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path, route_override="research/live_compare/general")
    _set_provider(runtime, _OkProvider())

    response = runtime.run_prompt("run ls", continue_session=False)

    assert response.route.task_signature == "research/live_compare/general", (
        f"Expected research/live_compare/general, got {response.route.task_signature!r}"
    )
    assert response.route.source == "override"


# ---------------------------------------------------------------------------
# Test 2 — CLI subprocess: --route flag is honoured end-to-end
# ---------------------------------------------------------------------------

_ROCKY_BIN = Path(__file__).parent.parent / ".venv" / "bin" / "rocky"


@pytest.mark.skipif(not _ROCKY_BIN.exists(), reason="rocky CLI not installed in venv")
@pytest.mark.skipif(
    not __import__("os").environ.get("ROCKY_LLM_SMOKE"),
    reason="Set ROCKY_LLM_SMOKE=1 with a live Ollama instance to run CLI subprocess tests",
)
def test_cli_route_override_flag(tmp_path: Path, monkeypatch) -> None:
    """Rocky CLI with --route emits the overridden task_signature in JSON output.

    The prompt 'list key metrics from the project' is lexically distinct from
    research (no explicit web/live markers) — without --route it resolves to
    repo/* or conversation/*.  With --route it must resolve to the given
    signature.

    Requires a live Ollama instance (ROCKY_LLM_SMOKE=1).
    """
    import os
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    proc = subprocess.run(
        [
            str(_ROCKY_BIN),
            "--route", "research/live_compare/general",
            "--json",
            "--freeze",
            "--cwd", str(tmp_path),
            "list key metrics from the project",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, (
        f"rocky exited non-zero.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    data = json.loads(proc.stdout)
    route = data.get("route", {})
    assert route.get("task_signature") == "research/live_compare/general", (
        f"Expected research/live_compare/general in route, got: {route}"
    )


# ---------------------------------------------------------------------------
# Test 3 — CF-4 control: no route_override = bit-identical lexical behavior
# ---------------------------------------------------------------------------

def test_no_route_override_preserves_default_lexical_routing(tmp_path: Path, monkeypatch) -> None:
    """Without route_override, a shell-flavored prompt must route to repo/* not research/*.

    This proves that adding the route_override parameter with default None does
    not alter existing call paths.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path)  # no route_override kwarg
    _set_provider(runtime, _OkProvider())

    response = runtime.run_prompt("run ls -la /tmp", continue_session=False)

    assert response.route.task_signature != "research/live_compare/general", (
        "Default routing must not produce research signature for a shell prompt"
    )
    assert response.route.task_class in (
        TaskClass.REPO, TaskClass.CONVERSATION, TaskClass.AUTOMATION
    ), f"Unexpected task_class: {response.route.task_class}"
    assert response.route.source != "override", (
        "source must not be 'override' when no route_override is set"
    )


# ---------------------------------------------------------------------------
# Test 4 — teach-guard preserved: _maybe_upgrade_route_from_project_context
#           runs unconditionally even when route_override is set
# ---------------------------------------------------------------------------

def test_teach_guard_call_is_unconditional_after_override(tmp_path: Path, monkeypatch) -> None:
    """Structural + minimal runtime assertion.

    Structural: inspect agent.py source to confirm _maybe_upgrade_route_from_project_context
    is called unconditionally after the if/else route-resolution block.

    Runtime: when override is set to conversation/general (no tool_families),
    a policy that would upgrade the route still runs and produces an upgrade —
    i.e., the teach-guard path is reachable even when the original route was
    produced by override.
    """
    # --- Structural assertion ---
    import rocky.core.agent as agent_module
    source = inspect.getsource(agent_module.AgentCore.run)
    override_idx = source.find("if route_override is not None:")
    guard_idx = source.find("_maybe_upgrade_route_from_project_context")
    assert override_idx != -1, "route_override branch not found in run()"
    assert guard_idx != -1, "_maybe_upgrade_route_from_project_context call not found in run()"
    assert guard_idx > override_idx, (
        "_maybe_upgrade_route_from_project_context must appear after the override block"
    )

    # --- Runtime assertion: teach-guard runs after override ---
    # Set up a policy that upgrades conversation/general to research/live_compare/general
    # on short "github repos right now"-style prompts.  Then set override to
    # conversation/general (no tool_families); the teach-guard should still fire.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    policy_dir = workspace / ".rocky" / "policies" / "learned" / "tool-use-refusal-ov"
    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_dir.joinpath("POLICY.md").write_text(
        """---
policy_id: tool-use-refusal-ov
name: tool-use-refusal-ov
description: Avoid false refusals regarding live web search availability.
scope: project
task_signatures:
  - conversation/general
generation: 1
failure_class: tool_use_refusal
promotion_state: candidate
feedback_excerpt: you must use web search and you do have search tools
required_behavior:
  - Attempt to use web search tools for real-time queries.
prohibited_behavior:
  - Refuse live queries by claiming a lack of search tools.
retrieval:
  triggers:
    - github
    - repos
    - right now
  keywords:
    - web search
    - live data
---

Use web search tools for live queries.
""",
        encoding="utf-8",
    )

    runtime = RockyRuntime.load_from(workspace, route_override="conversation/general")
    runtime.permissions.config.mode = "bypass"
    _set_provider(runtime, _OkProvider())

    response = runtime.run_prompt("github repos right now", continue_session=False)

    # _maybe_upgrade_route_from_project_context should upgrade from
    # conversation/general (set by override) to research/live_compare/general
    # via the single-declared policy.
    assert response.route.task_signature == "research/live_compare/general", (
        f"Teach-guard did not run after route_override — route stayed: "
        f"{response.route.task_signature!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 — invalid route override raises ValueError with helpful message
# ---------------------------------------------------------------------------

def test_invalid_route_override_raises_value_error(tmp_path: Path, monkeypatch) -> None:
    """route_override with an unknown signature raises ValueError listing valid ones."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    runtime = RockyRuntime.load_from(tmp_path, route_override="bogus/unknown")
    _set_provider(runtime, _OkProvider())

    with pytest.raises(ValueError, match="bogus/unknown"):
        runtime.run_prompt("do something", continue_session=False)
