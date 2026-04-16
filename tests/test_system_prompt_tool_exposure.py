Status: DONE
"""O3 — System-prompt tool-list accuracy + structured reroute error.

Verifies:
  (a) build_system_prompt advertises only the route's actual tool allowlist.
  (b) ToolRegistry.run() returns a structured tool_not_exposed ToolResult
      when the registry is configured with an exposed_names restriction.
  (c) _suggest_route_for_tool maps known tools to canonical route signatures.
  (d) ToolRegistry.tool_names is a frozenset of all registered tool names.
"""
from pathlib import Path

import pytest

from rocky.core.context import ContextPackage
from rocky.core.system_prompt import build_system_prompt
from rocky.tools.registry import ToolRegistry, _suggest_route_for_tool
from rocky.tools.base import ToolContext
from rocky.config.models import AppConfig
from rocky.core.permissions import PermissionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(tool_families: list[str], task_signature: str = "") -> ContextPackage:
    return ContextPackage(
        instructions=[],
        memories=[],
        skills=[],
        learned_policies=[],
        tool_families=tool_families,
    )


def _make_registry(
    tmp_path: Path,
    *,
    exposed_names: frozenset[str] | None = None,
    current_route: str = "",
) -> ToolRegistry:
    config = AppConfig()
    permissions = PermissionManager(config=config.permissions, workspace_root=tmp_path)
    tool_ctx = ToolContext(
        workspace_root=tmp_path,
        execution_root=tmp_path,
        artifacts_dir=tmp_path / ".rocky" / "artifacts",
        permissions=permissions,
        config=config,
    )
    return ToolRegistry(tool_ctx, exposed_names=exposed_names, current_route=current_route)


# ---------------------------------------------------------------------------
# 1. System prompt — repo/shell_execution route
# ---------------------------------------------------------------------------

def test_system_prompt_repo_route_exposes_shell_and_filesystem_tools() -> None:
    """repo/shell_execution exposes shell + filesystem tools, not web tools."""
    ctx = _make_context(["filesystem", "shell"], "repo/shell_execution")
    prompt = build_system_prompt(ctx, mode="bypass", task_signature="repo/shell_execution")

    tool_section_start = prompt.find("## Tool exposure")
    assert tool_section_start != -1, "## Tool exposure section missing"
    tool_section = prompt[tool_section_start:]

    assert "run_shell_command" in tool_section, "run_shell_command must be listed for repo/shell_execution"
    assert "read_file" in tool_section, "read_file must be listed for repo/shell_execution"
    assert "search_web" not in tool_section, (
        "search_web must NOT be listed for repo/shell_execution"
    )
    assert "All tools are available" not in prompt, (
        "'All tools are available' must be replaced with the route-scoped tool list"
    )


# ---------------------------------------------------------------------------
# 2. System prompt — research/live_compare/general route
# ---------------------------------------------------------------------------

def test_system_prompt_research_route_exposes_web_tools_not_shell() -> None:
    """research/live_compare/general exposes web+browser tools, not shell tools."""
    ctx = _make_context(["web", "browser"], "research/live_compare/general")
    prompt = build_system_prompt(
        ctx,
        mode="bypass",
        task_signature="research/live_compare/general",
    )

    tool_section_start = prompt.find("## Tool exposure")
    assert tool_section_start != -1, "## Tool exposure section missing"
    tool_section = prompt[tool_section_start:]

    assert "search_web" in tool_section or "fetch_url" in tool_section, (
        "At least one web tool must appear for research route"
    )
    assert "run_shell_command" not in tool_section, (
        "run_shell_command must NOT be listed for research/live_compare/general"
    )
    assert "All tools are available" not in prompt


def test_system_prompt_research_route_preserves_structural_anchors() -> None:
    """CF-4 control: non-tool-list structural parts of the prompt still exist."""
    ctx = _make_context(["web", "browser"], "research/live_compare/general")
    prompt = build_system_prompt(
        ctx,
        mode="bypass",
        task_signature="research/live_compare/general",
    )

    assert "You are Rocky" in prompt
    assert "Be concise, concrete, and operational" in prompt
    assert "## Tool exposure" in prompt


# ---------------------------------------------------------------------------
# 3. Structured reroute error on blocked tool
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blocked_tool", ["search_web", "fetch_url"])
def test_blocked_tool_returns_structured_reroute_error(
    tmp_path: Path, blocked_tool: str
) -> None:
    """Blocked tool call returns structured tool_not_exposed ToolResult."""
    repo_tools: frozenset[str] = frozenset(["run_shell_command", "read_file", "write_file"])
    registry = _make_registry(
        tmp_path,
        exposed_names=repo_tools,
        current_route="repo/shell_execution",
    )

    result = registry.run(blocked_tool, {})

    assert result.success is False, "Blocked tool call must return success=False"
    data = result.data
    assert isinstance(data, dict), f"data must be a dict, got {type(data)}"
    assert data.get("error") == "tool_not_exposed", (
        f"data['error'] must be 'tool_not_exposed', got {data.get('error')!r}"
    )
    assert data.get("tool") == blocked_tool
    reroute = data.get("reroute_to")
    assert reroute is not None and isinstance(reroute, str) and reroute, (
        f"data['reroute_to'] must be non-empty string, got {reroute!r}"
    )
    reason = data.get("reason")
    assert reason and isinstance(reason, str), (
        f"data['reason'] must be non-empty string, got {reason!r}"
    )


# ---------------------------------------------------------------------------
# 4. _suggest_route_for_tool unit tests
# ---------------------------------------------------------------------------

def test_suggest_route_for_search_web_returns_research_signature() -> None:
    result = _suggest_route_for_tool("search_web")
    assert result is not None
    assert "research" in result, f"Expected research signature, got {result!r}"


def test_suggest_route_for_run_shell_command_returns_repo_signature() -> None:
    result = _suggest_route_for_tool("run_shell_command")
    assert result is not None
    assert "repo" in result or "shell" in result, (
        f"Expected repo/shell signature, got {result!r}"
    )


def test_suggest_route_for_unknown_tool_returns_none() -> None:
    assert _suggest_route_for_tool("nonexistent_tool") is None
    assert _suggest_route_for_tool("") is None
    assert _suggest_route_for_tool("__totally_fake__") is None


# ---------------------------------------------------------------------------
# 5. tool_names property
# ---------------------------------------------------------------------------

def test_tool_names_is_frozenset_of_all_registered_names(tmp_path: Path) -> None:
    registry = _make_registry(tmp_path)
    names = registry.tool_names
    assert isinstance(names, frozenset), f"tool_names must be frozenset, got {type(names)}"
    for expected in ("run_shell_command", "read_file", "search_web"):
        assert expected in names, f"tool_names must contain {expected!r}, got {sorted(names)}"


def test_tool_names_independent_of_exposed_names_restriction(tmp_path: Path) -> None:
    """tool_names lists ALL registered tools even when exposed_names restricts run()."""
    repo_only: frozenset[str] = frozenset(["run_shell_command", "read_file"])
    registry = _make_registry(tmp_path, exposed_names=repo_only, current_route="repo/shell_execution")
    assert "search_web" in registry.tool_names
    assert "fetch_url" in registry.tool_names
    assert "run_shell_command" in registry.tool_names
