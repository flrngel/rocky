"""Route refinement — teach over-tagging scoped-policy guard (Phase 2.1, O1).

Bug reproduction source: `docs/xlfg/runs/run-20260412-013706/evidence/live/tree1_publish/
policies_learned_snapshot/workflow-correction-conversation-general/POLICY.md`.

`/teach` auto-generates policies whose frontmatter declares MULTIPLE task_signatures —
often `[conversation/general, research/live_compare/general, repo/shell_execution]` —
because the synthesizer over-generalizes. `AgentCore._maybe_upgrade_route_from_project_context`
then scores each declared signature with `_TASK_SIGNATURE_BIAS` (repo/shell_execution
carries the highest bias = +4) and picks the winner. A greeting-y prompt whose raw
route is `conversation/general` gets silently routed to `repo/shell_execution` with
`source=project_context, confidence=0.93` — even though the policy is supposed to be
a greeting correction.

Refined fix (run-20260413-124455): if the current route's task_signature is one of
the policy's declared signatures AND other signatures are also declared, the policy
is ambiguously scoped — skip it for the upgrade candidate search. Single-declared
policies still participate in inference-based upgrade (legacy tool-use-refusal case
at `tests/test_agent_runtime.py::test_learned_tool_refusal_policy_can_upgrade_conversation_route_to_research`).

This test uses the exact `POLICY.md` captured from the run-013706 bug repro —
not a hand-authored approximation — so the fix is grounded in observed reality.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rocky.app import RockyRuntime


_CAPTURED_TEACH_POLICY = Path(
    "docs/xlfg/runs/run-20260412-013706/evidence/live/tree1_publish/"
    "policies_learned_snapshot/workflow-correction-conversation-general/POLICY.md"
)


def _build_runtime_with_captured_policy(tmp_path: Path, monkeypatch) -> RockyRuntime:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    policy_dir = (
        workspace / ".rocky" / "policies" / "learned"
        / "workflow-correction-conversation-general"
    )
    policy_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / _CAPTURED_TEACH_POLICY
    if not src.exists():
        pytest.skip(
            f"Captured /teach policy evidence missing at {src}; "
            f"this test requires the run-20260412-013706 evidence tree."
        )
    shutil.copy(src, policy_dir / "POLICY.md")
    (policy_dir / "POLICY.meta.json").write_text(
        '{"policy_id": "workflow-correction-conversation-general", '
        '"promotion_state": "promoted"}\n',
        encoding="utf-8",
    )
    return RockyRuntime.load_from(workspace)


@pytest.mark.parametrize(
    "greeting_prompt",
    ["hi there", "say hello", "good morning", "hello briefly", "greet user"],
)
def test_ambiguously_scoped_teach_policy_does_not_reroute_conversation_to_shell(
    tmp_path: Path, monkeypatch, greeting_prompt: str
) -> None:
    """Bug reproduction — /teach over-declared policy must not cross-family hijack.

    The captured `/teach` policy declares `task_signatures: [conversation/general,
    research/live_compare/general, repo/shell_execution]`. Before the fix, a
    greeting-y prompt would upgrade to `repo/shell_execution` via
    `source=project_context`. After the fix, the conductor recognizes that the
    current route (conversation/general) is declared by the policy alongside
    other signatures — the policy is ambiguously scoped — and skips the upgrade.
    """
    runtime = _build_runtime_with_captured_policy(tmp_path, monkeypatch)
    agent = runtime.agent

    route = agent.router.decision_for_task_signature(
        "conversation/general",
        reasoning="initial conversation route",
        confidence=0.9,
        source="lexical",
    )
    assert route is not None
    route.tool_families = []

    upgraded = agent._maybe_upgrade_route_from_project_context(greeting_prompt, route)

    assert upgraded.task_signature == "conversation/general", (
        f"Teach over-tagging: prompt {greeting_prompt!r} with the captured /teach "
        f"policy declaring [conversation/general, research/..., repo/shell_execution] "
        f"must stay in conversation/general (declared alongside other signatures = "
        f"ambiguous scope = prefer current route). Got upgraded.task_signature="
        f"{upgraded.task_signature!r}, source={upgraded.source!r}."
    )
    assert upgraded.source != "project_context", (
        f"With the fix applied, the policy must NOT drive a project_context route "
        f"change for prompt {greeting_prompt!r}. Got source={upgraded.source!r}."
    )
