from __future__ import annotations

from rocky.core.context import ContextPackage
from rocky.core.system_prompt import build_system_prompt


def test_system_prompt_warns_against_inventing_prior_turns() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=[]),
        mode="bypass",
        user_prompt="what was my previous question?",
    )

    assert "Do not pretend to remember earlier turns" in prompt
    assert "keep created, copied, edited, and verified files inside the current workspace" in prompt


def test_system_prompt_pushes_multi_step_tool_use() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=["shell", "filesystem"]),
        mode="bypass",
        user_prompt="show me what python versions i have and where they live",
        task_signature="local/runtime_inspection",
    )

    assert "decompose the request into enough tool calls" in prompt
    assert "After each tool result, decide whether another tool is needed" in prompt
    assert "start with `inspect_runtime_versions`, then use at least one confirming shell command" in prompt


def test_system_prompt_guides_data_and_extraction_tasks() -> None:
    data_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=["filesystem", "data", "python"]),
        mode="bypass",
        user_prompt="analyze sales.csv",
        task_signature="data/spreadsheet/analysis",
    )
    extraction_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=["filesystem", "data", "python"]),
        mode="bypass",
        user_prompt="classify tickets.txt into json",
        task_signature="extract/general",
    )

    assert "first tool call should usually be `inspect_spreadsheet`" in data_prompt
    assert "use that exact path first instead of searching or guessing" in data_prompt
    assert "return the requested JSON directly" in extraction_prompt
    assert "Do not write output files unless the user explicitly asked" in extraction_prompt
    assert "prefer `run_python` to read and parse the source directly" in extraction_prompt
    assert "line prefixes" in extraction_prompt
    assert "Never create or mention output files" in extraction_prompt


def test_system_prompt_guides_shell_and_automation_tasks() -> None:
    shell_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=["filesystem", "shell", "python", "git"]),
        mode="bypass",
        user_prompt="execute ls and count the entries",
        task_signature="repo/shell_execution",
    )
    automation_prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=["filesystem", "shell", "python"]),
        mode="bypass",
        user_prompt="create a repeatable cleanup script and verify it",
        task_signature="automation/general",
    )

    assert "the first tool call should be `run_shell_command`" in shell_prompt
    assert "keep them inside the workspace instead of using `/tmp`" in shell_prompt
    assert "verify it with `run_shell_command` before answering" in automation_prompt
    assert "Keep the script path inside the workspace" in automation_prompt
    assert "Do not probe the environment or run verification commands before the file exists" in automation_prompt
