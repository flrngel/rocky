from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.tools.shell import inspect_shell_environment, read_shell_history


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
