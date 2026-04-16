Status: DONE  # xlfg artifact marker — O13 state-dir/cwd separation
"""
Tests for O13: state-dir / cwd separation.
Verifies that --state-dir decouples .rocky/ location from shell working directory.

Implementation: src/rocky/util/paths.py gains state_dir_override kwarg;
src/rocky/app.py gains state_dir kwarg; src/rocky/cli.py gains --state-dir flag.
"""
from pathlib import Path

import pytest

from rocky.cli import build_parser
from rocky.util.paths import discover_workspace


# ---------------------------------------------------------------------------
# 1. Path-discovery unit: override supplied
# ---------------------------------------------------------------------------

def test_discover_workspace_state_dir_override(tmp_path: Path) -> None:
    exec_dir = tmp_path / "exec"
    state_dir = tmp_path / "state"
    exec_dir.mkdir()
    state_dir.mkdir()

    ws = discover_workspace(exec_dir, state_dir_override=state_dir)

    # execution_root must point to exec dir
    assert ws.execution_root == exec_dir.resolve()

    # root (state root) must point to state dir, NOT exec dir
    assert ws.root == state_dir.resolve()

    # rocky_dir must live under state_dir
    assert ws.rocky_dir == state_dir.resolve() / ".rocky"

    # No state paths should leak back to exec_dir
    assert ws.memories_dir.is_relative_to(state_dir.resolve())
    assert ws.traces_dir.is_relative_to(state_dir.resolve())


# ---------------------------------------------------------------------------
# 2. CF-4 control: no override — execution_root and root both under cwd
# ---------------------------------------------------------------------------

def test_discover_workspace_no_override_parity(tmp_path: Path) -> None:
    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()

    ws = discover_workspace(exec_dir)

    # With no override, root must resolve to exec_dir (no .git/.rocky above it)
    assert ws.root == exec_dir.resolve()
    assert ws.execution_root == exec_dir.resolve()

    # rocky_dir must live under exec_dir
    assert ws.rocky_dir == exec_dir.resolve() / ".rocky"


# ---------------------------------------------------------------------------
# 3. Override == cwd: behavior must be identical to no-override (CF-4 parity)
# ---------------------------------------------------------------------------

def test_discover_workspace_override_equals_cwd(tmp_path: Path) -> None:
    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()

    ws_no_override = discover_workspace(exec_dir)
    ws_with_same = discover_workspace(exec_dir, state_dir_override=exec_dir)

    assert ws_no_override.root == ws_with_same.root
    assert ws_no_override.rocky_dir == ws_with_same.rocky_dir
    assert ws_no_override.execution_root == ws_with_same.execution_root


# ---------------------------------------------------------------------------
# 4. CLI-parser smoke: --state-dir wired correctly
# ---------------------------------------------------------------------------

def test_cli_parser_state_dir_present() -> None:
    parser = build_parser()
    args = parser.parse_args(["--state-dir", "/some/path", "do the thing"])
    assert args.state_dir == Path("/some/path")


def test_cli_parser_state_dir_default_none() -> None:
    parser = build_parser()
    args = parser.parse_args(["do the thing"])
    assert hasattr(args, "state_dir")
    assert args.state_dir is None


def test_cli_parser_state_dir_optional() -> None:
    """Parsing without --state-dir must not raise."""
    parser = build_parser()
    args = parser.parse_args(["some task"])
    assert args.state_dir is None
