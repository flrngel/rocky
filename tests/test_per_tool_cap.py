Status: DONE
"""Tests for O8: per-tool output cap overrides.

Pure-unit tests for the _tool_cap helper + YAML loader parsing + shell
integration with a custom cap.  No provider calls.
"""
from pathlib import Path

from rocky.config.models import ToolConfig
from rocky.tools.base import _tool_cap


# ---------------------------------------------------------------------------
# 1. Unit: per-tool override is honoured
# ---------------------------------------------------------------------------

def test_per_tool_override_returned() -> None:
    cfg = ToolConfig(tool_output_limits={"run_shell_command": 50})
    assert _tool_cap(cfg, "run_shell_command") == 50


def test_unset_tool_returns_global_default() -> None:
    cfg = ToolConfig(tool_output_limits={"run_shell_command": 50})
    assert _tool_cap(cfg, "read_file") == cfg.max_tool_output_chars


# ---------------------------------------------------------------------------
# 2. Empty dict falls back to global
# ---------------------------------------------------------------------------

def test_empty_limits_returns_global() -> None:
    cfg = ToolConfig(tool_output_limits={})
    assert _tool_cap(cfg, "run_shell_command") == cfg.max_tool_output_chars


# ---------------------------------------------------------------------------
# 3. Invalid override types are ignored (type safety)
# ---------------------------------------------------------------------------

def test_string_override_ignored() -> None:
    """A string value such as '50' is not a valid int override."""
    cfg = ToolConfig(tool_output_limits={"run_shell_command": "50"})  # type: ignore[arg-type]
    assert _tool_cap(cfg, "run_shell_command") == cfg.max_tool_output_chars


# ---------------------------------------------------------------------------
# 4. Zero / negative overrides are rejected
# ---------------------------------------------------------------------------

def test_zero_override_rejected() -> None:
    cfg = ToolConfig(tool_output_limits={"run_shell_command": 0})
    assert _tool_cap(cfg, "run_shell_command") == cfg.max_tool_output_chars


def test_negative_override_rejected() -> None:
    cfg = ToolConfig(tool_output_limits={"run_shell_command": -100})
    assert _tool_cap(cfg, "run_shell_command") == cfg.max_tool_output_chars


# ---------------------------------------------------------------------------
# 5. YAML loader parsing
# ---------------------------------------------------------------------------

def test_yaml_loader_parses_tool_output_limits(tmp_path: Path) -> None:
    """The config loader must materialise tool_output_limits from YAML."""
    from rocky.config.loader import ConfigLoader

    project_rocky = tmp_path / ".rocky"
    project_rocky.mkdir()
    config_yaml = project_rocky / "config.yaml"
    config_yaml.write_text(
        "tools:\n"
        "  max_tool_output_chars: 12000\n"
        "  tool_output_limits:\n"
        "    read_file: 30000\n"
        "    run_shell_command: 2000\n",
        encoding="utf-8",
    )

    global_root = tmp_path / "global"
    global_root.mkdir()

    loader = ConfigLoader(global_root=global_root, workspace_root=tmp_path)
    config = loader.load(create_defaults=False)

    assert config.tools.tool_output_limits == {
        "read_file": 30000,
        "run_shell_command": 2000,
    }


# ---------------------------------------------------------------------------
# 6. Shell integration: per-tool cap is applied inside run_shell_command
# ---------------------------------------------------------------------------

MARKER_PREFIX = "[rocky-truncated:"


def _make_tool_context(tool_output_limits: dict, workspace: Path):
    """Build a minimal ToolContext wired to the given workspace."""
    from rocky.config.models import AppConfig, PermissionConfig
    from rocky.core.permissions import PermissionManager
    from rocky.tools.base import ToolContext

    app_cfg = AppConfig(
        permissions=PermissionConfig(mode="bypass"),
        tools=ToolConfig(
            max_tool_output_chars=12000,
            tool_output_limits=tool_output_limits,
        ),
    )
    perms = PermissionManager(config=app_cfg.permissions, workspace_root=workspace)
    artifacts = workspace / ".rocky" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace_root=workspace,
        execution_root=workspace,
        artifacts_dir=artifacts,
        permissions=perms,
        config=app_cfg,
    )


def test_shell_integration_per_tool_cap_truncates(tmp_path: Path) -> None:
    """When run_shell_command has a 50-char cap, a 100-char output is truncated."""
    from rocky.tools.shell import run_shell_command

    ctx = _make_tool_context({"run_shell_command": 50}, tmp_path)
    result = run_shell_command(ctx, {"command": "python3 -c \"print('A' * 100)\""})

    stdout: str = result.data.get("stdout", "")
    assert MARKER_PREFIX in stdout, (
        f"Expected truncation marker in stdout, got: {stdout!r}"
    )
    kept = stdout.split(MARKER_PREFIX)[0]
    assert len(kept) <= 60, f"Kept portion too long ({len(kept)} chars): {kept!r}"


def test_shell_integration_global_cap_unchanged_without_override(tmp_path: Path) -> None:
    """Without a per-tool override the global 12000-char cap is used (no truncation for short output)."""
    from rocky.tools.shell import run_shell_command

    ctx = _make_tool_context({}, tmp_path)
    result = run_shell_command(ctx, {"command": "python3 -c \"print('A' * 100)\""})

    stdout: str = result.data.get("stdout", "")
    assert MARKER_PREFIX not in stdout, (
        f"Unexpected truncation marker in stdout: {stdout!r}"
    )
