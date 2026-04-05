from __future__ import annotations

import os
from pathlib import Path
import pytest

from rocky.app import RockyRuntime
from rocky.tools.python_exec import run_python
from rocky.tools.shell import inspect_runtime_versions, inspect_shell_environment, read_shell_history, run_shell_command


def test_inspect_shell_environment_returns_runtime_facts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("USER", "flrngel")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"

    result = inspect_shell_environment(runtime.tool_registry.context, {})

    assert result.success is True
    assert result.data["shell"] == "/bin/zsh"
    assert result.data["user"] == "flrngel"
    assert result.data["cwd"] == str(tmp_path)


def test_read_shell_history_reads_zsh_history_file(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    history_file = home / ".zsh_history"
    history_file.write_text(
        ": 1712000000:0;ls\n: 1712000001:0;pwd\n: 1712000002:0;rocky \"run echo 1+1\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/zsh")

    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.permissions.config.mode = "bypass"

    result = read_shell_history(runtime.tool_registry.context, {"limit": 2})

    assert result.success is True
    assert result.data["history_file"] == str(history_file)
    assert result.data["entries"] == ["pwd", 'rocky "run echo 1+1"']


def test_inspect_runtime_versions_finds_versioned_variants(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python3 = bin_dir / "python3"
    python314 = bin_dir / "python3.14"
    python3.write_text("#!/bin/sh\necho Python 3.14.3\n", encoding="utf-8")
    python314.write_text("#!/bin/sh\necho Python 3.14.3\n", encoding="utf-8")
    python3.chmod(0o755)
    python314.chmod(0o755)

    monkeypatch.setenv("PATH", str(bin_dir))

    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.permissions.config.mode = "bypass"

    result = inspect_runtime_versions(runtime.tool_registry.context, {"targets": ["python"]})

    assert result.success is True
    target = result.data["targets"][0]
    assert target["target"] == "python"
    assert target["exact_available"] is False
    commands = [item["command"] for item in target["matches"]]
    assert commands == ["python3", "python3.14"]


def test_run_shell_command_falls_back_to_workspace_for_external_readonly_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()

    runtime = RockyRuntime.load_from(workspace)
    runtime.permissions.config.mode = "bypass"

    result = run_shell_command(
        runtime.tool_registry.context,
        {"command": "pwd", "cwd": str(outside)},
    )

    assert result.success is True
    assert result.data["cwd"] == "."
    assert result.data["stdout"].strip() == str(workspace.resolve())
    assert result.metadata["cwd_fallback"] is True
    assert result.metadata["requested_cwd"] == str(outside.resolve())


def test_run_shell_command_keeps_rejecting_external_write_cwds(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()

    runtime = RockyRuntime.load_from(workspace)
    runtime.permissions.config.mode = "bypass"

    with pytest.raises(ValueError, match="Path escapes workspace"):
        run_shell_command(
            runtime.tool_registry.context,
            {"command": "touch nope.txt", "cwd": str(outside)},
        )


def test_run_shell_command_bootstraps_user_shell_rc_for_shell_functions(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("nvm() { echo \"nvm-from-rc $@\"; }\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/zsh")

    runtime = RockyRuntime.load_from(tmp_path / "workspace")
    runtime.permissions.config.mode = "bypass"

    result = run_shell_command(runtime.tool_registry.context, {"command": "nvm ls"})

    assert result.success is True
    assert "nvm-from-rc ls" in result.data["stdout"]


def test_run_shell_command_inherits_tool_env_overrides(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(
        tmp_path / "workspace",
        {"tools": {"env": {"HTTPS_PROXY": "http://proxy.internal:8080"}}},
    )
    runtime.permissions.config.mode = "bypass"

    result = run_shell_command(
        runtime.tool_registry.context,
        {"command": "printf '%s' \"$HTTPS_PROXY\""},
    )

    assert result.success is True
    assert result.data["stdout"] == "http://proxy.internal:8080"


def test_run_python_inherits_tool_env_overrides(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(
        tmp_path / "workspace",
        {"tools": {"env": {"HTTPS_PROXY": "http://proxy.internal:8080"}}},
    )
    runtime.permissions.config.mode = "bypass"

    result = run_python(
        runtime.tool_registry.context,
        {"code": "import os; print(os.environ.get('HTTPS_PROXY', ''))"},
    )

    assert result.success is True
    assert result.data["stdout"].strip() == "http://proxy.internal:8080"
