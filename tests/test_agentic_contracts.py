from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.core.messages import Message
from rocky.core.router import Router, TaskClass
from rocky.providers.base import ProviderResponse


@dataclass(frozen=True)
class Scenario:
    name: str
    prompt: str
    task_class: TaskClass
    task_signature: str
    driver: str
    tool_families: tuple[str, ...] = ()
    required_tool_names: tuple[str, ...] = ()
    output_kind: str = "plain"


SCENARIOS: list[Scenario] = [
    Scenario("meta_tools", "what tools do you have?", TaskClass.META, "meta/runtime", "meta"),
    Scenario("meta_skills", "what skills do you have?", TaskClass.META, "meta/runtime", "meta"),
    Scenario("meta_config", "show config", TaskClass.META, "meta/runtime", "meta"),
    Scenario("meta_provider", "what provider am i using right now?", TaskClass.META, "meta/runtime", "meta"),
    Scenario("meta_model", "what model am i using right now?", TaskClass.META, "meta/runtime", "meta"),
    Scenario("meta_status", "/status", TaskClass.META, "meta/runtime", "meta"),
    Scenario("conversation_hi", "hi", TaskClass.CONVERSATION, "conversation/general", "complete"),
    Scenario("conversation_repl", "explain what a REPL is", TaskClass.CONVERSATION, "conversation/general", "complete"),
    Scenario("conversation_story", "tell me a short story about rain", TaskClass.CONVERSATION, "conversation/general", "complete"),
    Scenario(
        "shell_exec_echo",
        "run echo 1+1",
        TaskClass.REPO,
        "repo/shell_execution",
        "tools",
        ("filesystem", "shell", "python", "git"),
        ("run_shell_command",),
    ),
    Scenario(
        "shell_exec_fenced",
        "execute command and find information about me\n```bash\nwhoami && id && pwd\n```",
        TaskClass.REPO,
        "repo/shell_execution",
        "tools",
        ("filesystem", "shell", "python", "git"),
        ("run_shell_command",),
    ),
    Scenario(
        "shell_exec_launch",
        "launch pwd && whoami",
        TaskClass.REPO,
        "repo/shell_execution",
        "tools",
        ("filesystem", "shell", "python", "git"),
        ("run_shell_command",),
    ),
    Scenario(
        "shell_exec_check",
        "check ls -1",
        TaskClass.REPO,
        "repo/shell_execution",
        "tools",
        ("filesystem", "shell", "python", "git"),
        ("run_shell_command",),
    ),
    Scenario(
        "shell_exec_python_version",
        "run python3 --version",
        TaskClass.REPO,
        "repo/shell_execution",
        "tools",
        ("filesystem", "shell", "python", "git"),
        ("run_shell_command",),
    ),
    Scenario(
        "shell_exec_env",
        "execute the following shell command: env | grep HOME",
        TaskClass.REPO,
        "repo/shell_execution",
        "tools",
        ("filesystem", "shell", "python", "git"),
        ("run_shell_command",),
    ),
    Scenario(
        "shell_inspect_shell",
        "what shell am i using",
        TaskClass.REPO,
        "repo/shell_inspection",
        "tools",
        ("shell", "filesystem"),
        ("inspect_shell_environment", "read_shell_history"),
    ),
    Scenario(
        "shell_inspect_where",
        "where am i",
        TaskClass.REPO,
        "repo/shell_inspection",
        "tools",
        ("shell", "filesystem"),
        ("inspect_shell_environment", "read_shell_history"),
    ),
    Scenario(
        "shell_inspect_user_home",
        "who am i and what is my home directory",
        TaskClass.REPO,
        "repo/shell_inspection",
        "tools",
        ("shell", "filesystem"),
        ("inspect_shell_environment", "read_shell_history"),
    ),
    Scenario(
        "shell_inspect_history",
        "show me 10 last history of current shell",
        TaskClass.REPO,
        "repo/shell_inspection",
        "tools",
        ("shell", "filesystem"),
        ("inspect_shell_environment", "read_shell_history"),
    ),
    Scenario(
        "shell_inspect_dir_and_shell",
        "what is my current directory and shell",
        TaskClass.REPO,
        "repo/shell_inspection",
        "tools",
        ("shell", "filesystem"),
        ("inspect_shell_environment", "read_shell_history"),
    ),
    Scenario(
        "shell_inspect_env_values",
        "what environment variable values do USER HOME SHELL have",
        TaskClass.REPO,
        "repo/shell_inspection",
        "tools",
        ("shell", "filesystem"),
        ("inspect_shell_environment", "read_shell_history"),
    ),
    Scenario(
        "runtime_python_versions",
        "what python versions do i have",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "runtime_python_versions_system",
        "what python versions in my system do i have",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "runtime_node_versions",
        "what node versions in my system do i have",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "runtime_ruby_versions",
        "what are ruby versions in my system list it",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "runtime_where_node",
        "where is node",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "runtime_bun_installed",
        "is bun installed",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "runtime_which_python",
        "which python",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "runtime_ruby_version",
        "what version of ruby",
        TaskClass.REPO,
        "local/runtime_inspection",
        "tools",
        ("shell",),
        ("inspect_runtime_versions",),
    ),
    Scenario(
        "repo_git_status",
        "in this repo, show current git status and last commit message",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "repo_modified_files",
        "what files are modified in this repo right now",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "repo_shell_history_impl",
        "find where shell history is implemented in this repo and tell me the file and function name",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "repo_readme_tui_choice",
        "read README.md and tell me why Rocky uses prompt_toolkit plus Rich",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "repo_cli_parser",
        "in this repo, which file defines the CLI parser",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "repo_command_aliases",
        "what command aliases exist in this repo",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "repo_config_wizard",
        "summarize the config wizard implementation in this repo",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "repo_chat_provider",
        "in this repo, find the provider that handles chat completions",
        TaskClass.REPO,
        "repo/general",
        "tools",
        ("filesystem", "shell", "git", "python"),
        ("read_file", "git_status"),
    ),
    Scenario(
        "data_spreadsheet_columns",
        "analyze this spreadsheet and tell me the key columns",
        TaskClass.DATA,
        "data/spreadsheet/analysis",
        "tools",
        ("filesystem", "data", "python"),
        ("inspect_spreadsheet", "read_sheet_range"),
    ),
    Scenario(
        "data_profile_xlsx",
        "profile data.xlsx and summarize anomalies",
        TaskClass.DATA,
        "data/spreadsheet/analysis",
        "tools",
        ("filesystem", "data", "python"),
        ("inspect_spreadsheet", "read_sheet_range"),
    ),
    Scenario(
        "data_compare_sheets",
        "inspect workbook sales.xlsx and compare sheets",
        TaskClass.DATA,
        "data/spreadsheet/analysis",
        "tools",
        ("filesystem", "data", "python"),
        ("inspect_spreadsheet", "read_sheet_range"),
    ),
    Scenario(
        "research_latest_release",
        "latest Python release and source link",
        TaskClass.RESEARCH,
        "research/live_compare/general",
        "tools",
        ("web", "browser"),
        ("search_web", "browser_render_page"),
        "sources",
    ),
    Scenario(
        "research_weather_compare",
        "compare sources for today's weather in San Francisco",
        TaskClass.RESEARCH,
        "research/live_compare/general",
        "tools",
        ("web", "browser"),
        ("search_web", "browser_render_page"),
        "sources",
    ),
    Scenario(
        "research_alternatives",
        "research prompt_toolkit alternatives and cite sources",
        TaskClass.RESEARCH,
        "research/live_compare/general",
        "tools",
        ("web", "browser"),
        ("search_web", "browser_render_page"),
        "sources",
    ),
    Scenario(
        "site_scrape_title",
        "scrape the title of https://example.com",
        TaskClass.SITE,
        "site/understanding/general",
        "tools",
        ("web", "browser", "filesystem"),
        ("fetch_url", "browser_render_page"),
        "sources",
    ),
    Scenario(
        "site_click_button",
        "click a button on a website and tell me what happened",
        TaskClass.SITE,
        "site/understanding/general",
        "tools",
        ("web", "browser", "filesystem"),
        ("fetch_url", "browser_render_page"),
        "sources",
    ),
    Scenario(
        "site_crawl_docs",
        "crawl a docs website and summarize the main sections",
        TaskClass.SITE,
        "site/understanding/general",
        "tools",
        ("web", "browser", "filesystem"),
        ("fetch_url", "browser_render_page"),
        "sources",
    ),
    Scenario(
        "extract_classify_json",
        "classify these support tickets into JSON",
        TaskClass.EXTRACTION,
        "extract/general",
        "tools",
        ("filesystem", "python", "data"),
        ("read_file", "run_python"),
        "json",
    ),
    Scenario(
        "extract_normalize_json",
        "normalize this list into JSON with a schema",
        TaskClass.EXTRACTION,
        "extract/general",
        "tools",
        ("filesystem", "python", "data"),
        ("read_file", "run_python"),
        "json",
    ),
    Scenario(
        "automation_backup",
        "automate a daily backup workflow",
        TaskClass.AUTOMATION,
        "automation/general",
        "tools",
        ("filesystem", "shell", "python"),
        ("run_shell_command", "run_python"),
    ),
    Scenario(
        "automation_archive_logs",
        "create a repeatable script to archive logs every night",
        TaskClass.AUTOMATION,
        "automation/general",
        "tools",
        ("filesystem", "shell", "python"),
        ("run_shell_command", "run_python"),
    ),
]


def _scenario_response_text(scenario: Scenario) -> str:
    if scenario.output_kind == "json":
        return '{"ok": true}'
    if scenario.output_kind == "sources":
        return "Sources: https://example.com\nok"
    return "ok"


class _ContractProvider:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.complete_calls: list[dict] = []
        self.tool_calls: list[dict] = []

    def complete(self, system_prompt, messages, stream=False, event_handler=None) -> ProviderResponse:
        self.complete_calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
            }
        )
        return ProviderResponse(text=_scenario_response_text(self.scenario), raw={})

    def run_with_tools(self, system_prompt, messages, tools, execute_tool, max_rounds=8, event_handler=None) -> ProviderResponse:
        self.tool_calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "tools": tools,
                "max_rounds": max_rounds,
            }
        )
        tool_name = self.scenario.required_tool_names[0] if self.scenario.required_tool_names else tools[0]["function"]["name"]
        return ProviderResponse(
            text=_scenario_response_text(self.scenario),
            raw={"rounds": []},
            tool_events=[
                {
                    "type": "tool_result",
                    "name": tool_name,
                    "arguments": {},
                    "text": "{}",
                    "success": True,
                }
            ],
        )


class _ContractProviderRegistry:
    def __init__(self, provider: _ContractProvider) -> None:
        self.provider = provider

    def provider_for_task(self, needs_tools: bool = False):
        return self.provider


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_router_contract_for_diverse_prompts(scenario: Scenario) -> None:
    route = Router().route(scenario.prompt)

    assert route.task_class == scenario.task_class
    assert route.task_signature == scenario.task_signature
    for family in scenario.tool_families:
        assert family in route.tool_families


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[scenario.name for scenario in SCENARIOS])
def test_runtime_contract_for_diverse_prompts(tmp_path: Path, monkeypatch, scenario: Scenario) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    runtime = RockyRuntime.load_from(workspace)
    provider = _ContractProvider(scenario)
    registry = _ContractProviderRegistry(provider)
    runtime.provider_registry = registry
    runtime.agent.provider_registry = registry

    response = runtime.run_prompt(scenario.prompt, continue_session=False)

    assert response.route.task_class == scenario.task_class
    assert response.route.task_signature == scenario.task_signature
    if scenario.driver == "meta":
        assert provider.complete_calls == []
        assert provider.tool_calls == []
        assert response.trace["provider"] == "deterministic"
        return

    if scenario.driver == "complete":
        assert len(provider.complete_calls) == 1
        assert provider.tool_calls == []
        assert response.trace["provider"] == "_ContractProvider"
        return

    assert provider.complete_calls == []
    assert len(provider.tool_calls) == 1
    assert response.trace["provider"] == "_ContractProvider"
    assert response.trace["selected_tools"]
    tool_names = {tool["function"]["name"] for tool in provider.tool_calls[0]["tools"]}
    for name in scenario.required_tool_names:
        assert name in tool_names
