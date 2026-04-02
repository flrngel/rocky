from __future__ import annotations

from rocky.core.router import RouteDecision, Lane, TaskClass
from rocky.core.verifiers import VerifierRegistry


def test_verifier_requires_tools_for_shell_tasks() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="medium",
        reasoning="Shell inspection request",
        tool_families=["shell", "filesystem"],
        task_signature="repo/shell_inspection",
    )

    result = verifier.verify(
        prompt="show me 10 last history of current shell",
        route=route,
        task_class=route.task_class,
        output="```bash\nhistory | tail -10\n```",
        tool_events=[],
    )

    assert result.status == "fail"
    assert "no tools were used" in result.message.lower()


def test_verifier_requires_tools_for_repo_inspection_prompts() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="medium",
        reasoning="Repo inspection request",
        tool_families=["filesystem", "git", "shell"],
        task_signature="repo/general",
    )

    result = verifier.verify(
        prompt="in this repo, show current git status and last commit message",
        route=route,
        task_class=route.task_class,
        output="The repo looks clean.",
        tool_events=[],
    )

    assert result.status == "fail"
    assert "inspect the repo with tools" in result.message.lower()


def test_verifier_requires_tools_for_runtime_inspection_prompts() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="medium",
        reasoning="Runtime inspection request",
        tool_families=["shell"],
        task_signature="local/runtime_inspection",
    )

    result = verifier.verify(
        prompt="what python versions do i have",
        route=route,
        task_class=route.task_class,
        output="Python 3.11.9",
        tool_events=[],
    )

    assert result.status == "fail"
    assert "inspect the local runtime" in result.message.lower()


def test_verifier_requires_shell_tool_for_shell_execution() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="medium",
        reasoning="Shell execution request",
        tool_families=["filesystem", "shell", "python", "git"],
        task_signature="repo/shell_execution",
    )

    result = verifier.verify(
        prompt="execute ls and count the entries",
        route=route,
        task_class=route.task_class,
        output="100 entries",
        tool_events=[
            {"type": "tool_result", "name": "list_files", "success": True},
            {"type": "tool_result", "name": "read_file", "success": True},
        ],
    )

    assert result.status == "fail"
    assert "run_shell_command" in result.message


def test_verifier_requires_follow_up_step_for_multi_step_shell_execution() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="medium",
        reasoning="Shell execution request",
        tool_families=["filesystem", "shell", "python", "git"],
        task_signature="repo/shell_execution",
    )

    result = verifier.verify(
        prompt="run a command that creates note.txt, then read it and stat it",
        route=route,
        task_class=route.task_class,
        output="Created note.txt.",
        tool_events=[
            {"type": "tool_result", "name": "run_shell_command", "success": True},
        ],
    )

    assert result.status == "fail"
    assert "follow-up tool step" in result.message.lower()


def test_verifier_requires_runtime_inspection_tool() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.REPO,
        risk="medium",
        reasoning="Runtime inspection request",
        tool_families=["shell"],
        task_signature="local/runtime_inspection",
    )

    result = verifier.verify(
        prompt="what python versions do i have",
        route=route,
        task_class=route.task_class,
        output="Python 3.14.3",
        tool_events=[
            {"type": "tool_result", "name": "run_shell_command", "success": True},
        ],
    )

    assert result.status == "fail"
    assert "inspect_runtime_versions" in result.message


def test_verifier_requires_execution_for_verifying_automation() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.AUTOMATION,
        risk="medium",
        reasoning="Automation task",
        tool_families=["filesystem", "shell", "python"],
        task_signature="automation/general",
    )

    result = verifier.verify(
        prompt="create a repeatable cleanup script and verify it",
        route=route,
        task_class=route.task_class,
        output="Created cleanup script.",
        tool_events=[
            {"type": "tool_result", "name": "write_file", "success": True},
        ],
    )

    assert result.status == "fail"
    assert "verify the automation" in result.message.lower()


def test_verifier_requires_multiple_steps_for_spreadsheet_analysis() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.DATA,
        risk="medium",
        reasoning="Spreadsheet analysis task",
        tool_families=["filesystem", "data", "python"],
        task_signature="data/spreadsheet/analysis",
    )

    result = verifier.verify(
        prompt="inspect data/metrics.xlsx, compare the Summary and Regions sample rows, and count the sheets",
        route=route,
        task_class=route.task_class,
        output="Two sheets.",
        tool_events=[
            {"type": "tool_result", "name": "inspect_spreadsheet", "success": True},
        ],
    )

    assert result.status == "fail"
    assert "two spreadsheet-analysis steps" in result.message.lower()


def test_verifier_accepts_recovered_automation_shell_retries() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.AUTOMATION,
        risk="medium",
        reasoning="Automation task",
        tool_families=["filesystem", "shell", "python"],
        task_signature="automation/general",
    )

    result = verifier.verify(
        prompt="create a repeatable cleanup script for tmp artifacts and verify it",
        route=route,
        task_class=route.task_class,
        output="Created cleanup_tmp.sh and verified it.",
        tool_events=[
            {"type": "tool_result", "name": "run_shell_command", "success": False},
            {"type": "tool_result", "name": "write_file", "success": True},
            {"type": "tool_result", "name": "run_shell_command", "success": True},
        ],
    )

    assert result.status == "pass"


def test_verifier_accepts_recovered_automation_after_intermediate_tool_failure() -> None:
    verifier = VerifierRegistry()
    route = RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.AUTOMATION,
        risk="medium",
        reasoning="Automation task",
        tool_families=["filesystem", "shell", "python"],
        task_signature="automation/general",
    )

    result = verifier.verify(
        prompt="build a tiny shell script project in this empty workspace and tell me the exact output",
        route=route,
        task_class=route.task_class,
        output="Done. `sh report.sh` printed `360`.",
        tool_events=[
            {"type": "tool_result", "name": "write_file", "success": True},
            {"type": "tool_result", "name": "run_shell_command", "success": True},
            {"type": "tool_result", "name": "write_file", "success": False},
            {"type": "tool_result", "name": "run_shell_command", "success": True},
        ],
    )

    assert result.status == "pass"
