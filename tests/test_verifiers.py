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
