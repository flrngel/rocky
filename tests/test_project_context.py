from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.providers.base import ProviderResponse
from rocky.tools.filesystem import read_file, write_file
from rocky.tools.shell import inspect_shell_environment, run_shell_command


class _CompleteProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls.append({"system_prompt": system_prompt, "messages": messages, "mode": "complete"})
        return ProviderResponse(text=self.text)

    def run_with_tools(
        self,
        system_prompt,
        messages,
        tools,
        execute_tool,
        max_rounds,
        event_handler=None,
    ) -> ProviderResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "tools": tools,
                "max_rounds": max_rounds,
                "mode": "run_with_tools",
            }
        )
        return ProviderResponse(text=self.text, tool_events=[])


class _Registry:
    def __init__(self, provider) -> None:
        self.provider = provider

    def provider_for_task(self, needs_tools: bool = False):
        return self.provider


def _set_provider(runtime: RockyRuntime, provider) -> None:
    registry = _Registry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry


def test_new_session_loads_recent_project_handoff_into_context(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = RockyRuntime.load_from(workspace)

    seed_provider = _CompleteProvider(
        "Implemented parser flow in src/parser.py. Keep focusing on config loading and workspace memory."
    )
    _set_provider(runtime, seed_provider)
    runtime.run_prompt(
        "Build the parser in src/parser.py and keep focusing on config loading.",
        continue_session=False,
    )

    follow_up_provider = _CompleteProvider("Continuing the project.")
    _set_provider(runtime, follow_up_provider)
    runtime.run_prompt("continue the work from the current project", continue_session=False)

    assert follow_up_provider.calls
    system_prompt = str(follow_up_provider.calls[0]["system_prompt"])
    assert "## Project handoff" in system_prompt
    assert "src/parser.py" in system_prompt
    assert "config loading" in system_prompt

    context = runtime.current_context()
    assert context["workspace_focus"]["execution_cwd"] == "."
    assert context["handoffs"]


def test_subdirectory_runtime_defaults_shell_to_execution_root(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("USER", "rockytester")

    root = tmp_path / "workspace"
    (root / ".git").mkdir(parents=True)
    subdir = root / "pkg" / "ui"
    subdir.mkdir(parents=True)

    runtime = RockyRuntime.load_from(subdir)
    runtime.permissions.config.mode = "bypass"

    result = run_shell_command(runtime.tool_registry.context, {"command": "pwd"})
    assert result.success is True
    assert result.data["cwd"] == "pkg/ui"
    assert result.data["stdout"].strip() == str(subdir.resolve())

    inspected = inspect_shell_environment(runtime.tool_registry.context, {})
    assert inspected.success is True
    assert inspected.data["cwd"] == str(subdir.resolve())


def test_subdirectory_runtime_prefers_local_writes_but_can_read_repo_root(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    root = tmp_path / "workspace"
    (root / ".git").mkdir(parents=True)
    subdir = root / "pkg" / "ui"
    subdir.mkdir(parents=True)
    (root / "README.md").write_text("# Root readme\n", encoding="utf-8")

    runtime = RockyRuntime.load_from(subdir)
    runtime.permissions.config.mode = "bypass"

    root_read = read_file(runtime.tool_registry.context, {"path": "README.md"})
    assert root_read.success is True
    assert "Root readme" in root_read.data
    assert root_read.metadata["path"] == "README.md"

    local_write = write_file(runtime.tool_registry.context, {"path": "notes.txt", "content": "hello\n"})
    assert local_write.success is True
    assert local_write.data["path"] == "pkg/ui/notes.txt"
    assert (subdir / "notes.txt").exists()
