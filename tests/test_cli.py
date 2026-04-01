from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from rocky.cli import main
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
    names = ["help", "config", "configure", "setup", "set-up"]

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
        self.prompts: list[str] = []

    def run_prompt(self, text: str, stream: bool, event_handler):
        self.prompts.append(text)
        return SimpleNamespace(
            text="done",
            route=RouteDecision(Lane.DIRECT, TaskClass.CONVERSATION, "low", "test"),
            verification={"status": "pass", "message": "ok"},
            usage={},
            trace={},
        )


def test_cli_reads_task_from_stdin(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda cwd, cli_overrides=None: runtime)
    monkeypatch.setattr("sys.stdin", _FakeStdin("summarize this\n"))

    exit_code = main(["--json"])

    assert exit_code == 0
    assert runtime.prompts == ["summarize this"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"] == "done"


def test_cli_maps_command_aliases(monkeypatch, capsys) -> None:
    runtime = _FakeRuntime()
    monkeypatch.setattr("rocky.cli.RockyRuntime.load_from", lambda cwd, cli_overrides=None: runtime)

    exit_code = main(["configure", "--json"])

    assert exit_code == 0
    assert runtime.commands.calls == ["/configure"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "config"
