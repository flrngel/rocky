from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from rocky.cli import _maybe_run_first_launch_wizard, main
from rocky.commands.registry import CommandResult
from rocky.core.router import Lane, RouteDecision, TaskClass


class _FakeStdin(io.StringIO):
    def isatty(self) -> bool:
        return False


class _FakeProviderConfig:
    model = "fake-model"
    base_url = "http://example.test/v1"


class _FakeConfig:
    active_provider = "ollama"

    def provider(self, name: str):
        return _FakeProviderConfig()


class _FakeCommands:
    names = ["help", "config", "configure", "setup", "set-up", "memory"]

    def __init__(self) -> None:
        self.calls: list[str] = []

    def handle(self, text: str) -> CommandResult:
        self.calls.append(text)
        return CommandResult("config", "ok", {"ok": True})


class _FakeRuntime:
    def __init__(self) -> None:
        self.config = _FakeConfig()
        self.permissions = SimpleNamespace(ask_callback=None)
        self.commands = _FakeCommands()
        self.freeze_enabled = False
        self.verbose_enabled = False
        self.prompts: list[str] = []
        self.continue_session_values: list[bool] = []
        self.freeze_values: list[bool | None] = []

    def run_prompt(
        self,
        text: str,
        stream: bool,
        event_handler,
        continue_session: bool = True,
        freeze: bool | None = None,
        **kwargs,
    ):
        self.prompts.append(text)
        self.continue_session_values.append(continue_session)
        self.freeze_values.append(freeze)
        return SimpleNamespace(
            text="done",
            route=RouteDecision(Lane.DIRECT, TaskClass.CONVERSATION, "low", "test"),
            verification={"status": "pass", "message": "ok"},
            usage={},
            trace={},
        )


def test_cli_reads_task_from_stdin(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda cwd, cli_overrides=None, freeze=False, verbose=False, **kwargs: runtime)
    monkeypatch.setattr("sys.stdin", _FakeStdin("summarize this\n"))

    exit_code = main(["--json"])

    assert exit_code == 0
    assert runtime.prompts == ["summarize this"]
    assert runtime.continue_session_values == [False]
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "done"


def test_cli_maps_command_aliases(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda cwd, cli_overrides=None, freeze=False, verbose=False, **kwargs: runtime)

    exit_code = main(["configure", "--json"])

    assert exit_code == 0
    assert runtime.commands.calls == ["/config"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "config"


def test_cli_routes_multiword_memory_commands(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda cwd, cli_overrides=None, freeze=False, verbose=False, **kwargs: runtime)

    exit_code = main(["memory", "add", "style", "Prefer terse output.", "--json"])

    assert exit_code == 0
    assert runtime.commands.calls == ["/memory add style Prefer terse output."]
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "config"


def test_cli_can_opt_into_session_continuation(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda cwd, cli_overrides=None, freeze=False, verbose=False, **kwargs: runtime)

    exit_code = main(["--continue", "say hi", "--json"])

    assert exit_code == 0
    assert runtime.continue_session_values == [True]
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "done"


def test_first_launch_noninteractive_writes_default_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("rocky.cli._interactive_terminal", lambda: False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _maybe_run_first_launch_wizard(workspace, Console(file=io.StringIO()), allow_wizard=True)

    assert (tmp_path / "home" / ".config" / "rocky" / "config.yaml").exists()


def test_cli_version_prints_and_exits_without_runtime(monkeypatch, capsys) -> None:
    monkeypatch.setattr("rocky.cli._maybe_run_first_launch_wizard", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("wizard should not run")))
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runtime should not load")))

    exit_code = main(["--version"])

    from rocky import __version__ as _rocky_version

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == f"rocky {_rocky_version}"


def test_cli_verification_output_is_plain_text(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda cwd, cli_overrides=None, freeze=False, verbose=False, **kwargs: runtime)

    def _run_prompt(
        text: str,
        stream: bool,
        event_handler,
        continue_session: bool = True,
        freeze: bool | None = None,
        **kwargs,
    ):
        runtime.prompts.append(text)
        runtime.continue_session_values.append(continue_session)
        runtime.freeze_values.append(freeze)
        return SimpleNamespace(
            text="provider failure",
            route=RouteDecision(Lane.DIRECT, TaskClass.CONVERSATION, "low", "test"),
            verification={"status": "fail", "message": "[Errno 61] Connection refused"},
            usage={},
            trace={},
        )

    runtime.run_prompt = _run_prompt

    exit_code = main(["hi"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "verification" in output
    assert "[Errno 61] Connection refused" in output
    assert "[yellow]" not in output


def test_cli_freeze_flag_reaches_runtime(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    load_calls: list[bool] = []

    def _load_from(cwd, cli_overrides=None, freeze=False, verbose=False, **kwargs):
        load_calls.append(freeze)
        runtime.freeze_enabled = freeze
        runtime.verbose_enabled = verbose
        return runtime

    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", _load_from)

    exit_code = main(["--freeze", "say hi", "--json"])

    assert exit_code == 0
    assert load_calls == [True]
    assert runtime.freeze_values == [True]
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "done"


def test_cli_verbose_flag_reaches_runtime(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    load_calls: list[bool] = []

    def _load_from(cwd, cli_overrides=None, freeze=False, verbose=False, **kwargs):
        load_calls.append(verbose)
        runtime.verbose_enabled = verbose
        return runtime

    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", _load_from)

    exit_code = main(["--verbose", "say hi", "--json"])

    assert exit_code == 0
    assert load_calls == [True]
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "done"
