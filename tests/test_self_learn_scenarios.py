"""End-to-end self-learn verification scenarios.

Driven by the Rocky Hyperlearning v2 PRD (Phase 0). Each scenario exercises the
real learning pipeline (`RockyRuntime`, `LearnedPolicyLoader`,
`LearnedPolicyRetriever`, `LearningManager`) on `tmp_path` workspaces without
mocking the learning subsystem and without requiring a live LLM provider.

Invariants protected here:
  1. Candidate learned policies never emit hard constraints in the system
     prompt, even when retrieved.
  2. A learned policy written by one runtime is reachable from a fresh runtime
     loaded against the same workspace (cross-process reuse).
  3. Blanking the on-disk policy store flips the reuse assertion — there is no
     hidden fallback that lets a "learned" outcome survive without the real
     file. This is the anti-tamper gate that prevents hard-coding.
  4. `/learned review` filters to candidate policies only; `/help` hides the
     deprecated `/learn` and `/policies` surfaces while keeping the `/learn`
     alias functional for one transition cycle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.config.models import LearningConfig
from rocky.core.context import ContextPackage
from rocky.core.system_prompt import build_system_prompt
from rocky.learning.manager import LearningManager
from rocky.learning.policies import LearnedPolicyLoader


_CANDIDATE_POLICY_BODY = """---
policy_id: {policy_id}
scope: project
description: {description}
promotion_state: {promotion_state}
retrieval:
  triggers:
    - merge decisions
    - exact json
task_signatures:
  - repo/shell_execution
required_behavior:
  - Return exactly the JSON contract the teacher requested.
prohibited_behavior:
  - Include commentary or prose outside the requested JSON.
---

# Learned corrective policy

Return the JSON contract verbatim when the teacher pins a format.
"""


def _build_runtime(tmp_path: Path, monkeypatch) -> RockyRuntime:
    """Construct an isolated RockyRuntime using tmp_path as HOME + workspace."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return RockyRuntime.load_from(workspace)


def _seed_learn_trace(runtime: RockyRuntime) -> None:
    """Seed the fields `runtime.learn()` requires without needing a live LLM."""
    runtime.agent.last_prompt = "execute pending_catalog.sh and return the exact json merge decisions"
    runtime.agent.last_answer = '{"result": "incorrect"}'
    runtime.agent.last_trace = {
        "route": {"task_signature": "repo/shell_execution"},
        "selected_tools": ["run_shell_command"],
        "verification": {"failure_class": "answer_contract_violation"},
        "thread": {
            "current_thread": {
                "thread_id": "thread_sc",
                "task_signature": "repo/shell_execution",
                "task_family": "repo",
                "prompt_history": [{"prompt": "execute pending_catalog.sh and return the exact json merge decisions"}],
            }
        },
    }


def _write_hand_authored_policy(
    workspace: Path,
    policy_id: str,
    promotion_state: str,
    *,
    description: str = "Return only the exact merge-decisions JSON.",
) -> Path:
    """Drop a hand-authored POLICY.md + POLICY.meta.json pair onto disk."""
    root = workspace / ".rocky" / "policies" / "learned" / policy_id
    root.mkdir(parents=True, exist_ok=True)
    policy_path = root / "POLICY.md"
    policy_path.write_text(
        _CANDIDATE_POLICY_BODY.format(
            policy_id=policy_id,
            description=description,
            promotion_state=promotion_state,
        ),
        encoding="utf-8",
    )
    meta_path = root / "POLICY.meta.json"
    meta_payload = {
        "policy_id": policy_id,
        "policy_path": str(policy_path),
        "scope": "project",
        "generation": 1,
        "published": True,
        "promotion_state": promotion_state,
        "metadata": {
            "policy_id": policy_id,
            "description": description,
            "promotion_state": promotion_state,
            "retrieval": {"triggers": ["merge decisions", "exact json"]},
            "task_signatures": ["repo/shell_execution"],
            "required_behavior": ["Return exactly the JSON contract the teacher requested."],
            "prohibited_behavior": ["Include commentary or prose outside the requested JSON."],
        },
    }
    meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
    return policy_path


def _policy_to_record(policy) -> dict:
    """Mirror `ContextBuilder.build`'s construction of the learned_policies dict."""
    return {
        "name": policy.name,
        "scope": policy.scope,
        "origin": policy.origin,
        "generation": policy.generation,
        "path": str(policy.path),
        "description": policy.description,
        "text": policy.body[:6000],
        "promotion_state": policy.metadata.get("promotion_state", "promoted"),
        "failure_class": policy.metadata.get("failure_class"),
        "task_family": policy.metadata.get("task_family"),
        "required_behavior": list(policy.metadata.get("required_behavior") or []),
        "prohibited_behavior": list(policy.metadata.get("prohibited_behavior") or []),
        "evidence_requirements": list(policy.metadata.get("evidence_requirements") or []),
        "feedback_excerpt": policy.metadata.get("feedback_excerpt"),
        "reflection_source": policy.metadata.get("reflection_source"),
        "reflection_confidence": policy.metadata.get("reflection_confidence"),
        "storage_format": policy.storage_format,
    }


def _promote_on_disk(policy_path: Path) -> None:
    """Flip a candidate policy to promoted state on disk."""
    text = policy_path.read_text(encoding="utf-8")
    policy_path.write_text(
        text.replace("promotion_state: candidate", "promotion_state: promoted", 1),
        encoding="utf-8",
    )
    meta_path = policy_path.parent / "POLICY.meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["promotion_state"] = "promoted"
        inner = dict(meta.get("metadata") or {})
        inner["promotion_state"] = "promoted"
        meta["metadata"] = inner
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def test_teach_to_reuse_cross_process(tmp_path, monkeypatch) -> None:
    """Policy written by one runtime is retrievable by a fresh runtime after promotion."""
    runtime_a = _build_runtime(tmp_path, monkeypatch)
    _seed_learn_trace(runtime_a)

    result = runtime_a.learn("Return only the exact JSON merge-decisions contract.")
    assert result["published"] is True, f"expected publication, got {result}"
    policy_id = result["policy_id"]
    policy_path = Path(result["policy_path"])
    assert policy_path.exists()

    _promote_on_disk(policy_path)

    workspace = runtime_a.workspace.root
    del runtime_a

    runtime_b = RockyRuntime.load_from(workspace)
    retrieved = runtime_b.policy_retriever.retrieve(
        "execute pending_catalog.sh and return the exact json merge decisions",
        "repo/shell_execution",
    )
    retrieved_ids = [policy.policy_id for policy in retrieved]
    assert policy_id in retrieved_ids, (
        f"promoted policy {policy_id!r} should be retrievable from a fresh runtime; "
        f"retriever returned {retrieved_ids!r}"
    )


def test_candidate_policy_never_hard(tmp_path, monkeypatch) -> None:
    """A candidate policy's prohibited rule MUST NOT emit a hard constraint."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    policy_path = _write_hand_authored_policy(workspace, "never-hard", "candidate")

    loader = LearnedPolicyLoader(workspace)
    policies = loader.load_all()
    assert policies, "loader must see the hand-authored policy"
    policy = policies[0]
    record = _policy_to_record(policy)

    candidate_prompt = build_system_prompt(
        ContextPackage(
            instructions=[],
            memories=[],
            skills=[],
            learned_policies=[record],
            tool_families=["shell", "filesystem"],
        ),
        mode="bypass",
        user_prompt="merge decisions",
        task_signature="repo/shell_execution",
    )
    # Phase 2.3 packer rename: `## Learned constraints` → `## Hard constraints`;
    # `## Learned policies` → `## Procedural brief`. Candidate-never-hard invariant holds.
    assert "## Hard constraints" not in candidate_prompt, (
        "candidate policies must not produce a Hard constraints block"
    )
    assert "Do not: Include commentary or prose outside the requested JSON." not in candidate_prompt
    assert "## Procedural brief" in candidate_prompt, (
        "candidate policies remain visible informationally in the procedural brief"
    )

    _promote_on_disk(policy_path)
    loader_promoted = LearnedPolicyLoader(workspace)
    policies_promoted = loader_promoted.load_all()
    policy_promoted = policies_promoted[0]
    assert policy_promoted.metadata.get("promotion_state") == "promoted"
    promoted_record = _policy_to_record(policy_promoted)

    promoted_prompt = build_system_prompt(
        ContextPackage(
            instructions=[],
            memories=[],
            skills=[],
            learned_policies=[promoted_record],
            tool_families=["shell", "filesystem"],
        ),
        mode="bypass",
        user_prompt="merge decisions",
        task_signature="repo/shell_execution",
    )
    assert "## Hard constraints" in promoted_prompt
    assert "Do not: Include commentary or prose outside the requested JSON." in promoted_prompt


def test_cross_process_policy_carryover(tmp_path, monkeypatch) -> None:
    """A learned policy persists through the `LearnedPolicyLoader` process boundary."""
    runtime = _build_runtime(tmp_path, monkeypatch)
    _seed_learn_trace(runtime)
    result = runtime.learn("Return the exact merge-decisions JSON only.")
    assert result["published"] is True
    policy_id = result["policy_id"]
    workspace = runtime.workspace.root
    del runtime

    loader = LearnedPolicyLoader(workspace)
    policies = loader.load_all()
    assert policy_id in [policy.policy_id for policy in policies], (
        f"fresh loader must see the disk-persisted policy {policy_id!r}"
    )


def test_tamper_detection_forces_scenario_to_fail_when_store_is_blanked(
    tmp_path, monkeypatch
) -> None:
    """Anti-tamper: blanking the policy file MUST cause the reuse assertion to flip to negative.

    This is the load-bearing invariant for "never hard-code to pass the
    scenario". If a future implementer adds a compiled fallback, hard-codes a
    match on the query string, or bypasses the on-disk loader, this test
    fails because the retriever still sees a match after tampering.
    """
    workspace_ok = tmp_path / "workspace_ok"
    workspace_ok.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home_ok"))
    (tmp_path / "home_ok").mkdir()
    runtime_ok = RockyRuntime.load_from(workspace_ok)
    _seed_learn_trace(runtime_ok)
    ok_result = runtime_ok.learn("Return only the exact JSON merge-decisions contract.")
    assert ok_result["published"] is True
    ok_policy_id = ok_result["policy_id"]
    _promote_on_disk(Path(ok_result["policy_path"]))
    del runtime_ok

    runtime_ok_fresh = RockyRuntime.load_from(workspace_ok)
    positive_ids = [
        policy.policy_id
        for policy in runtime_ok_fresh.policy_retriever.retrieve(
            "execute pending_catalog.sh and return the exact json merge decisions",
            "repo/shell_execution",
        )
    ]
    assert ok_policy_id in positive_ids, (
        "positive control must observe the learned policy before tampering; "
        f"retriever returned {positive_ids!r}"
    )
    del runtime_ok_fresh

    workspace_tampered = tmp_path / "workspace_tampered"
    workspace_tampered.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home_tamp"))
    (tmp_path / "home_tamp").mkdir()
    runtime_tamp = RockyRuntime.load_from(workspace_tampered)
    _seed_learn_trace(runtime_tamp)
    tamp_result = runtime_tamp.learn("Return only the exact JSON merge-decisions contract.")
    tamp_policy_id = tamp_result["policy_id"]
    tamp_policy_path = Path(tamp_result["policy_path"])
    _promote_on_disk(tamp_policy_path)

    tamp_policy_path.write_text("", encoding="utf-8")
    meta_path = tamp_policy_path.parent / "POLICY.meta.json"
    if meta_path.exists():
        meta_path.unlink()
    del runtime_tamp

    runtime_tamp_fresh = RockyRuntime.load_from(workspace_tampered)
    tampered_ids = [
        policy.policy_id
        for policy in runtime_tamp_fresh.policy_retriever.retrieve(
            "execute pending_catalog.sh and return the exact json merge decisions",
            "repo/shell_execution",
        )
    ]
    assert tamp_policy_id not in tampered_ids, (
        "tampered (blanked + meta-deleted) policy MUST NOT be reachable; "
        f"retriever still returned {tampered_ids!r}, suggesting a hidden fallback"
    )


def test_learned_review_lists_only_candidate_policies(tmp_path, monkeypatch) -> None:
    """`/learned review` must filter to policies with promotion_state == candidate."""
    runtime = _build_runtime(tmp_path, monkeypatch)
    workspace = runtime.workspace.root
    candidate_id = "sc-candidate"
    promoted_id = "sc-promoted"
    _write_hand_authored_policy(workspace, candidate_id, "candidate")
    promoted_path = _write_hand_authored_policy(workspace, promoted_id, "promoted")
    _promote_on_disk(promoted_path)

    review_result = runtime.commands.handle("/learned review")
    data = review_result.data or {}
    review_ids = {str(row.get("policy_id")) for row in data.get("candidates", [])}
    assert review_ids == {candidate_id}, (
        f"/learned review should return only candidates; got {review_ids!r}"
    )

    full_result = runtime.commands.handle("/learned")
    full_data = full_result.data or {}
    full_ids = {str(row.get("policy_id")) for row in full_data.get("learned", [])}
    assert {candidate_id, promoted_id} <= full_ids, (
        f"/learned must still list all policies; got {full_ids!r}"
    )


def test_candidate_policy_does_not_drive_judge_constraint_records(tmp_path, monkeypatch) -> None:
    """Candidate policies must NOT produce records that the judge/repair path treats as hard.

    AgentCore._learned_constraint_records is a second consumer of
    ContextPackage.learned_policies (separate from build_system_prompt) that
    feeds _judge_learned_constraints and _repair_learned_constraint_output.
    PRD §11's "Crucial change" requires the candidate-never-hard invariant to
    hold across ALL consumers, not just the system prompt.
    """
    from rocky.core.agent import AgentCore

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    candidate_path = _write_hand_authored_policy(workspace, "cand-judge", "candidate")
    promoted_path = _write_hand_authored_policy(workspace, "promo-judge", "promoted")
    _promote_on_disk(promoted_path)

    loader = LearnedPolicyLoader(workspace)
    policies = {policy.policy_id: policy for policy in loader.load_all()}
    records_input = [_policy_to_record(policies["cand-judge"]), _policy_to_record(policies["promo-judge"])]

    dummy_context = ContextPackage(
        instructions=[],
        memories=[],
        skills=[],
        learned_policies=records_input,
        tool_families=["shell"],
    )
    class _Stub:
        _truncate_text = AgentCore._truncate_text
        _learned_constraint_records = AgentCore._learned_constraint_records

    records = _Stub()._learned_constraint_records(dummy_context)
    record_names = {rec["name"] for rec in records}
    assert "cand-judge" not in record_names, (
        "candidate policy leaked into the judge constraint records; "
        f"judge would enforce it as hard. records={record_names!r}"
    )
    assert "promo-judge" in record_names, (
        "promoted policy must still feed the judge constraint path"
    )


def test_help_hides_learn_and_policies(tmp_path, monkeypatch) -> None:
    """`/help` hides `/learn` and `/policies`; `/learn` alias still dispatches."""
    runtime = _build_runtime(tmp_path, monkeypatch)
    help_text = runtime.commands.handle("/help").text
    assert "/policies" not in help_text, "/policies must be hidden from help output"
    assert "/learn <feedback>" not in help_text, "/learn must be hidden from help output"

    learn_result = runtime.commands.handle("/learn some correction")
    assert learn_result.name == "learn", (
        f"`/learn` alias must still dispatch to runtime.learn; got {learn_result.name!r}"
    )

    policies_result = runtime.commands.handle("/policies")
    assert policies_result.name == "error", (
        f"`/policies` must return Unknown-command error; got {policies_result.name!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
