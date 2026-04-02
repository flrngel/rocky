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


def test_verifier_requires_successful_live_price_lookup_for_current_price_prompt() -> None:
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
        prompt="what's the date today? use cli to get exact date and check the nike price of today",
        route=route,
        task_class=route.task_class,
        output="Date is 2026-04-02.",
        tool_events=[
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"stdout": "2026-04-02\\n", "stderr": ""}}',
            },
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"stdout": "Edge: Too Many Requests", "stderr": ""}}',
            },
        ],
    )

    assert result.status == "fail"
    assert "retry the current price lookup" in result.message.lower()


def test_verifier_accepts_successful_live_price_lookup_for_current_price_prompt() -> None:
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
        prompt="what's the date today? use cli to get exact date and check the nike price of today",
        route=route,
        task_class=route.task_class,
        output="Date is 2026-04-02 and Nike is 44.63.",
        tool_events=[
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"stdout": "2026-04-02\\n", "stderr": ""}}',
            },
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"stdout": "NKE.US,20260401,220023,46.555,46.83,44.56,44.63,114225664,\\n", "stderr": ""}}',
            },
        ],
    )

    assert result.status == "pass"


def test_verifier_accepts_recovered_live_price_lookup_after_retry() -> None:
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
        prompt="use command to get exact date. and then check the nike stock's price of today",
        route=route,
        task_class=route.task_class,
        output="Date is 2026-04-02 and Nike closed at 44.56 on 2026-04-01.",
        tool_events=[
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"stdout": "2026-04-02\\n", "stderr": ""}}',
            },
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": False,
                "text": '{"success": false, "data": {"stdout": "", "stderr": "jq: parse error: Invalid numeric literal"}}',
            },
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"stdout": "NKE.US,20260401,220023,46.555,46.83,44.56,44.63,114225664,\\n", "stderr": ""}}',
            },
        ],
    )

    assert result.status == "pass"


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


def test_verifier_requires_exact_command_mention_for_exact_automation_output() -> None:
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
        prompt="build a tiny shell script project and tell me the exact output",
        route=route,
        task_class=route.task_class,
        output="Done. Output is now 360.",
        tool_events=[
            {"type": "tool_result", "name": "write_file", "success": True},
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"command": "sh report.sh", "stdout": "360\\n", "stderr": ""}}',
            },
        ],
    )

    assert result.status == "fail"
    assert "exact script or command" in result.message.lower()


def test_verifier_accepts_exact_command_mention_for_exact_automation_output() -> None:
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
        prompt="build a tiny shell script project and tell me the exact output",
        route=route,
        task_class=route.task_class,
        output="Ran `sh report.sh` and it printed `360`.",
        tool_events=[
            {"type": "tool_result", "name": "write_file", "success": True},
            {
                "type": "tool_result",
                "name": "run_shell_command",
                "success": True,
                "text": '{"success": true, "data": {"command": "sh report.sh", "stdout": "360\\n", "stderr": ""}}',
            },
        ],
    )

    assert result.status == "pass"
