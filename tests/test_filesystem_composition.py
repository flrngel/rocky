Status: DONE
"""O4 — Research route filesystem composition (issue C2).

Verifies that ``tool_families_override`` additively composes extra tool
families on top of a read-only route profile, bypassing the
``READ_ONLY_TASK_SIGNATURES`` gate *only* for the explicitly overridden
families. Default behavior (no override) is bit-identical to today (CF-4).

Tests:
  1. Happy path: override adds filesystem write to research route.
  2. CF-4 control: no override -> write_file absent on research route.
  3. No global mutation: override does not leak across registry instances.
  4. Partial override: shell-only override does NOT add filesystem write.
  5. CLI arg parsing: --tools parses correctly for single, multi, and absent.
  6. Runtime threading: RockyRuntime.load_from stores tool_families_override.
"""

from pathlib import Path

import pytest

from rocky.cli import build_parser
from rocky.config.models import AppConfig
from rocky.core.permissions import PermissionManager
from rocky.tools.base import ToolContext
from rocky.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(tmp_path: Path) -> ToolRegistry:
    config = AppConfig()
    permissions = PermissionManager(config=config.permissions, workspace_root=tmp_path)
    tool_ctx = ToolContext(
        workspace_root=tmp_path,
        execution_root=tmp_path,
        artifacts_dir=tmp_path / ".rocky" / "artifacts",
        permissions=permissions,
        config=config,
    )
    return ToolRegistry(tool_ctx)


_RESEARCH_SIG = "research/live_compare/general"
_RESEARCH_FAMILIES = ["web", "browser"]  # default families for this route


# ---------------------------------------------------------------------------
# 1. Happy path — override adds filesystem write
# ---------------------------------------------------------------------------

def test_override_adds_write_file_to_research_route(tmp_path: Path) -> None:
    """tool_families_override=["filesystem"] makes write_file available."""
    registry = _make_registry(tmp_path)
    tools = registry.select_for_task(
        _RESEARCH_FAMILIES,
        _RESEARCH_SIG,
        tool_families_override=["filesystem"],
    )
    names = {t.name for t in tools}
    assert "write_file" in names, (
        f"write_file must be available when filesystem is in override; got {names}"
    )
    # Override is ADDITIVE — default research tools must still be present.
    assert "search_web" in names, (
        f"search_web (default research tool) must still be present; got {names}"
    )
    assert "fetch_url" in names, (
        f"fetch_url (default research tool) must still be present; got {names}"
    )


# ---------------------------------------------------------------------------
# 2. CF-4 control — no override: write_file absent
# ---------------------------------------------------------------------------

def test_no_override_write_file_absent_on_research_route(tmp_path: Path) -> None:
    """Without override, research route does not expose write_file (CF-4)."""
    registry = _make_registry(tmp_path)
    tools = registry.select_for_task(
        _RESEARCH_FAMILIES,
        _RESEARCH_SIG,
        # No tool_families_override
    )
    names = {t.name for t in tools}
    assert "write_file" not in names, (
        f"write_file must NOT be available on research route without override; got {names}"
    )


# ---------------------------------------------------------------------------
# 3. No global mutation: override does not leak across registry instances
# ---------------------------------------------------------------------------

def test_override_does_not_leak_to_fresh_registry(tmp_path: Path) -> None:
    """Happy-path override on one registry must not affect a subsequent fresh one."""
    # First call WITH override.
    reg1 = _make_registry(tmp_path)
    tools_with = reg1.select_for_task(
        _RESEARCH_FAMILIES,
        _RESEARCH_SIG,
        tool_families_override=["filesystem"],
    )
    assert "write_file" in {t.name for t in tools_with}

    # Second call on a fresh registry WITHOUT override.
    reg2 = _make_registry(tmp_path)
    tools_without = reg2.select_for_task(
        _RESEARCH_FAMILIES,
        _RESEARCH_SIG,
        # No override
    )
    names_without = {t.name for t in tools_without}
    assert "write_file" not in names_without, (
        f"write_file must NOT appear in fresh registry without override; got {names_without}"
    )


# ---------------------------------------------------------------------------
# 4. Partial override: shell-only does NOT add filesystem write
# ---------------------------------------------------------------------------

def test_shell_only_override_does_not_add_write_file(tmp_path: Path) -> None:
    """override=["shell"] adds shell tools but not filesystem write_file."""
    registry = _make_registry(tmp_path)
    tools = registry.select_for_task(
        _RESEARCH_FAMILIES,
        _RESEARCH_SIG,
        tool_families_override=["shell"],
    )
    names = {t.name for t in tools}
    # Shell tool should be present (shell is in override).
    assert "run_shell_command" in names, (
        f"run_shell_command must be available when shell is in override; got {names}"
    )
    # Filesystem write must NOT be available (filesystem is NOT in override).
    assert "write_file" not in names, (
        f"write_file must NOT be available when only shell is overridden; got {names}"
    )


# ---------------------------------------------------------------------------
# 5. CLI arg parsing
# ---------------------------------------------------------------------------

def test_cli_tools_single_family() -> None:
    """--tools filesystem parses to ["filesystem"]."""
    args = build_parser().parse_args(["--tools", "filesystem", "some task"])
    assert args.tools == ["filesystem"]


def test_cli_tools_multiple_families() -> None:
    """--tools filesystem,web parses to ["filesystem", "web"]."""
    args = build_parser().parse_args(["--tools", "filesystem,web", "some task"])
    assert args.tools == ["filesystem", "web"]


def test_cli_tools_absent_is_none() -> None:
    """Omitting --tools results in args.tools being None."""
    args = build_parser().parse_args(["some task"])
    assert args.tools is None


# ---------------------------------------------------------------------------
# 6. Runtime threading
# ---------------------------------------------------------------------------

def test_runtime_stores_tool_families_override(tmp_path: Path, monkeypatch) -> None:
    """RockyRuntime.load_from threads tool_families_override onto the runtime."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    from rocky.app import RockyRuntime

    runtime = RockyRuntime.load_from(
        cwd=tmp_path,
        freeze=True,
        tool_families_override=["filesystem"],
    )
    assert runtime.tool_families_override == ["filesystem"], (
        f"Expected tool_families_override=['filesystem'], got {runtime.tool_families_override!r}"
    )


def test_runtime_no_override_is_none(tmp_path: Path, monkeypatch) -> None:
    """Without --tools, runtime.tool_families_override is None."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    from rocky.app import RockyRuntime

    runtime = RockyRuntime.load_from(
        cwd=tmp_path,
        freeze=True,
    )
    assert runtime.tool_families_override is None, (
        f"Expected tool_families_override=None, got {runtime.tool_families_override!r}"
    )
