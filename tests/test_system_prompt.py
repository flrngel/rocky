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

    assert "first tool call must be `inspect_spreadsheet`" in data_prompt
    assert "`inspect_spreadsheet` works for CSV files too" in data_prompt
    assert "Do not use `run_python` as your first spreadsheet step" in data_prompt
    assert "use that exact path first instead of searching or guessing" in data_prompt
    assert "Do not stop after `inspect_spreadsheet` alone" in data_prompt
    assert "return the requested JSON directly" in extraction_prompt
    assert "Do not write output files unless the user explicitly asked" in extraction_prompt
    assert "prefer `run_python` to read and parse the source directly" in extraction_prompt
    assert "use `glob_paths` first and then `stat_path` or `read_file`" in extraction_prompt
    assert "Use at least two steps for extraction work" in extraction_prompt
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
    assert "do not collapse that into one tool call" in shell_prompt
    assert "such as `x.sh`" in shell_prompt
    assert "execute that workspace file directly" in shell_prompt
    assert "permission denied" in shell_prompt
    assert "returns structured text such as JSON" in shell_prompt
    assert "current command output from this turn is the source of truth" in shell_prompt
    assert "Do not substitute previous traces, memories, or handoff summaries" in shell_prompt
    assert "auth, permission, network, or other error payload" in shell_prompt
    assert "did not ask for a result file" in shell_prompt
    assert "verify it with `run_shell_command` before answering" in automation_prompt
    assert "Keep the script path inside the workspace" in automation_prompt
    assert "Do not probe the environment or run verification commands before the file exists" in automation_prompt
    assert "first successful tool call should usually be `write_file`" in automation_prompt
    assert "do at most one lightweight inspection" in automation_prompt
    assert "Do not use shell redirection, heredocs, `tee`, or inline interpreter one-liners" in automation_prompt
    assert "mention the exact script or command you ran and the exact observed output" in automation_prompt
    assert "at least three successful tool steps" in automation_prompt
    assert "reread it with `read_file`" in automation_prompt
    assert "within your first five successful tool calls" in automation_prompt


def test_system_prompt_guides_repo_lookup_follow_up_reads() -> None:
    prompt = build_system_prompt(
        ContextPackage(instructions=[], memories=[], skills=[], tool_families=["filesystem", "git"]),
        mode="bypass",
        user_prompt="in this repo, find where shell history is implemented and tell me the file and function name",
        task_signature="repo/general",
    )

    assert "do not stop at search hits alone" in prompt
    assert "After `grep_files` or `list_files`, read the most likely file" in prompt
    assert "Repeated search-only loops" in prompt
