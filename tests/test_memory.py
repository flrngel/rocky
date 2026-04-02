from __future__ import annotations

from pathlib import Path

from rocky.app import RockyRuntime
from rocky.providers.base import ProviderResponse


class _CompleteProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(text=self.text)


class _ToolProvider:
    def __init__(self, text: str, tool_events: list[dict] | None = None) -> None:
        self.text = text
        self.tool_events = tool_events or []
        self.calls = 0

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(text=self.text, raw={"rounds": []}, tool_events=self.tool_events)


class _FailingProvider:
    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        raise RuntimeError("provider offline")


class _Registry:
    def __init__(self, provider) -> None:
        self.provider = provider

    def provider_for_task(self, needs_tools: bool = False):
        return self.provider


def _runtime(tmp_path: Path, monkeypatch, workspace_name: str = "workspace") -> RockyRuntime:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / workspace_name
    workspace.mkdir(parents=True, exist_ok=True)
    return RockyRuntime.load_from(workspace)


def _set_provider(runtime: RockyRuntime, provider) -> None:
    registry = _Registry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry


def test_one_shot_success_writes_project_auto_memory(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    provider = _CompleteProvider(
        "Use prompt_toolkit and Rich for the TUI. Keep all state inside .rocky. README.md is an important project path."
    )
    _set_provider(runtime, provider)

    runtime.run_prompt("Build a CLI-first agent and keep all state inside .rocky.", continue_session=False)

    auto_files = sorted(runtime.workspace.memories_dir.joinpath("auto").glob("*.json"))
    assert auto_files
    assert runtime.workspace.memories_dir.joinpath("project_brief.md").exists()
    inventory = runtime.memory_list()
    assert inventory["project_auto"]
    assert inventory["global_manual"] == []


def test_repl_and_one_shot_both_participate_in_project_auto_memory(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    provider = _CompleteProvider(
        "Prefer concise CLI output. Keep state inside .rocky. docs/TUI_RESEARCH.md is an important project path."
    )
    _set_provider(runtime, provider)

    runtime.run_prompt("Build the CLI output system and keep state inside .rocky.", continue_session=False)
    first_count = len(runtime.memory_list()["project_auto"])

    provider.text = "Use prompt_toolkit and Rich. Verify workflows after changes."
    runtime.run_prompt("Improve the TUI workflow and verify changes after editing.", continue_session=True)

    assert len(runtime.memory_list()["project_auto"]) >= first_count


def test_subdirectory_uses_workspace_root_memory_bucket(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "workspace"
    subdir = root / "pkg" / "ui"
    subdir.mkdir(parents=True, exist_ok=True)

    runtime_root = RockyRuntime.load_from(root)
    provider = _CompleteProvider("Keep all state inside .rocky. src/ui.py is an important project path.")
    _set_provider(runtime_root, provider)
    runtime_root.run_prompt("Build the workspace UI and keep all state inside .rocky.", continue_session=False)

    runtime_sub = RockyRuntime.load_from(subdir)
    assert runtime_sub.workspace.root == root
    assert runtime_sub.memory_list()["project_auto"]


def test_unrelated_workspace_has_separate_project_auto_memory(tmp_path: Path, monkeypatch) -> None:
    runtime_one = _runtime(tmp_path, monkeypatch, "workspace_one")
    provider = _CompleteProvider("Keep all state inside .rocky.")
    _set_provider(runtime_one, provider)
    runtime_one.run_prompt("Build the first project and keep all state inside .rocky.", continue_session=False)

    runtime_two = _runtime(tmp_path, monkeypatch, "workspace_two")
    assert runtime_two.memory_list()["project_auto"] == []


def test_memory_commands_manage_global_manual_memory_only(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)

    added = runtime.commands.handle('/memory add style "Prefer plain text output."')
    assert added.data["ok"] is True
    assert runtime.memory_list()["project_auto"] == []
    assert len(runtime.memory_list()["global_manual"]) == 1

    updated = runtime.commands.handle('/memory set style "Prefer terse output."')
    assert updated.data["ok"] is True
    shown = runtime.commands.handle("/memory show global_manual:style")
    assert shown.data["memory"]["text"] == "Prefer terse output."

    removed = runtime.commands.handle("/memory remove style")
    assert removed.data["ok"] is True
    assert runtime.memory_list()["global_manual"] == []


def test_memory_command_rejects_project_auto_mutation(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    provider = _CompleteProvider("Keep all state inside .rocky. README.md is an important project path.")
    _set_provider(runtime, provider)
    runtime.run_prompt("Build the project and keep all state inside .rocky.", continue_session=False)

    name = runtime.memory_list()["project_auto"][0]["name"]
    result = runtime.commands.handle(f'/memory set project_auto:{name} "nope"')

    assert result.data["ok"] is False
    assert "read-only" in result.text


def test_memory_list_and_show_cover_project_and_global_scopes(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    provider = _CompleteProvider("Use prompt_toolkit and Rich. Keep state inside .rocky.")
    _set_provider(runtime, provider)
    runtime.run_prompt("Build the TUI and keep state inside .rocky.", continue_session=False)
    runtime.memory_add("style", "Prefer concise output.")

    listed = runtime.commands.handle("/memory list")

    assert listed.data["memory"]["project_auto"]
    assert listed.data["memory"]["global_manual"]
    auto_name = listed.data["memory"]["project_auto"][0]["name"]
    global_name = listed.data["memory"]["global_manual"][0]["name"]
    assert runtime.commands.handle(f"/memory show project_auto:{auto_name}").data["ok"] is True
    assert runtime.commands.handle(f"/memory show global_manual:{global_name}").data["ok"] is True


def test_project_brief_is_included_for_follow_up_queries(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    provider = _CompleteProvider("Use prompt_toolkit and Rich for the TUI. Keep state inside .rocky.")
    _set_provider(runtime, provider)
    runtime.run_prompt("Build the terminal UI and keep state inside .rocky.", continue_session=False)

    context = runtime.context_builder.build("make the interface nicer", "conversation/general", [])

    assert context.memories
    assert context.memories[0]["kind"] == "project_brief"


def test_project_auto_memories_rank_ahead_of_global_manual(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    provider = _CompleteProvider("Use prompt_toolkit and Rich for the TUI.")
    _set_provider(runtime, provider)
    runtime.run_prompt("Build the prompt_toolkit TUI with Rich output.", continue_session=False)
    runtime.memory_add("tui-note", "Use prompt_toolkit for interactive editing.")

    context = runtime.context_builder.build("improve prompt_toolkit output", "conversation/general", [])

    non_brief = [item for item in context.memories if item["kind"] != "project_brief"]
    assert non_brief
    assert non_brief[0]["scope"] == "project_auto"


def test_global_manual_memory_is_available_across_workspaces(tmp_path: Path, monkeypatch) -> None:
    runtime_one = _runtime(tmp_path, monkeypatch, "workspace_one")
    runtime_one.memory_add("operator-style", "Prefer terse output for all coding tasks.")

    runtime_two = _runtime(tmp_path, monkeypatch, "workspace_two")
    context = runtime_two.context_builder.build("keep the coding output terse", "conversation/general", [])

    assert any(item["scope"] == "global_manual" for item in context.memories)


def test_failed_runs_do_not_write_project_auto_memory(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    _set_provider(runtime, _FailingProvider())

    runtime.run_prompt("Build the CLI and keep state inside .rocky.", continue_session=False)

    assert runtime.memory_list()["project_auto"] == []


def test_warn_runs_do_not_write_project_auto_memory(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    provider = _ToolProvider("Market sentiment is positive.")
    _set_provider(runtime, provider)

    runtime.run_prompt("latest market research about ai", continue_session=False)

    assert runtime.memory_list()["project_auto"] == []


def test_memory_commands_do_not_create_project_auto_memory(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)

    runtime.commands.handle("/memory list")

    assert runtime.memory_list()["project_auto"] == []
