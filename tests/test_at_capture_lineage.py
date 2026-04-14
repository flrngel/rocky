"""T7 — at-capture teach-lineage linking (Phase 2.2).

When `/teach`'s correction is reused on a subsequent turn, `capture_project_memory`
runs autonomously and persists memory artifacts under a fresh turn-lineage.
Pre-Phase-2.2, those turn-lineage registrations were NOT linked to the
teach-lineage that drove the reuse — so `/undo` on the teach lineage left the
derived memories stranded (the "derived-autonomous leak" tracked by
`tests/test_self_learn_live.py::test_sl_undo_behavioral_correction_fully_gone`).

Fix: during capture, look up each reused policy's teach-lineage via the ledger
(`LearningLedgerStore.find_teach_lineage_for_policy`) and register the captured
artifacts under that teach-lineage IN ADDITION to the turn-lineage. Autonomous
capture for non-teach-reuse turns is untouched (CF-4).

This test is deterministic — no live LLM, no subprocess. It exercises the
lineage linkage directly via the public ledger API + the app helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.learning.ledger import LearningRecord, new_lineage_id
from rocky.util.time import utc_iso


def _make_teach_record(policy_id: str, teach_lineage_id: str) -> LearningRecord:
    stamp = utc_iso()
    return LearningRecord(
        id=teach_lineage_id,
        kind="procedure",
        scope="project",
        authority="teacher",
        promotion_state="candidate",
        activation_mode="soft",
        task_signature="repo/shell_execution",
        task_family="repo",
        failure_class=None,
        triggers=["install", "deps"],
        required_behavior=["Use pnpm add for install"],
        prohibited_behavior=["Do not use npm install"],
        evidence=["User corrected: use pnpm, not npm."],
        lineage={
            "id": teach_lineage_id,
            "policy_id": policy_id,
            "thread_id": "thread_test",
        },
        created_at=stamp,
        updated_at=stamp,
        origin={"type": "teacher_feedback", "feedback": "Use pnpm instead of npm"},
        reuse_stats={"reuse_count": 0, "verified_success_count": 0},
    )


def test_find_teach_lineage_for_policy_round_trip(tmp_path: Path, monkeypatch) -> None:
    """The ledger helper returns the teach-lineage for a given policy_id.

    Prereq for `_active_teach_lineages` — this is the load-bearing lookup that
    lets the app link autonomous capture to the reused teach-lineage.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    teach_lineage_id = new_lineage_id("teach")
    policy_id = "pnpm-preference-correction"
    runtime.ledger.append(_make_teach_record(policy_id, teach_lineage_id))

    found = runtime.ledger.find_teach_lineage_for_policy(policy_id)
    assert found == teach_lineage_id, (
        f"find_teach_lineage_for_policy({policy_id!r}) must return the teach "
        f"lineage id. Got {found!r}; expected {teach_lineage_id!r}."
    )

    # Missing id returns None (not an exception).
    assert runtime.ledger.find_teach_lineage_for_policy("") is None
    assert runtime.ledger.find_teach_lineage_for_policy("nonexistent") is None


def test_find_teach_lineage_ignores_rolled_back_records(tmp_path: Path, monkeypatch) -> None:
    """A rolled-back teach record must not be surfaced as an active teach-lineage.

    Without this guard, a post-undo reuse turn would re-link fresh derived
    memories to the rolled-back lineage, creating a zombie-link.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    teach_lineage_id = new_lineage_id("teach")
    policy_id = "pnpm-preference-correction"
    runtime.ledger.append(_make_teach_record(policy_id, teach_lineage_id))
    runtime.ledger.mark_rolled_back(teach_lineage_id)

    found = runtime.ledger.find_teach_lineage_for_policy(policy_id)
    assert found is None, (
        f"Rolled-back teach lineage {teach_lineage_id!r} must not be returned "
        f"as an active linkage target for policy {policy_id!r}. Got {found!r}."
    )


def test_active_teach_lineages_resolves_reused_policies(tmp_path: Path, monkeypatch) -> None:
    """`_active_teach_lineages(trace)` maps trace.selected_policies to teach-lineage ids."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    teach_lineage_id = new_lineage_id("teach")
    policy_id = "pnpm-preference-correction"
    runtime.ledger.append(_make_teach_record(policy_id, teach_lineage_id))

    trace = {"selected_policies": [policy_id]}
    resolved = runtime._active_teach_lineages(trace)
    assert resolved == [teach_lineage_id], (
        f"_active_teach_lineages must resolve reused policy ids to their teach "
        f"lineages. Got {resolved!r}."
    )

    # No-teach-reuse turn returns empty list — CF-4 preservation.
    assert runtime._active_teach_lineages({}) == []
    assert runtime._active_teach_lineages({"selected_policies": []}) == []
    assert runtime._active_teach_lineages({"selected_policies": ["unknown"]}) == []


def test_register_capture_artifacts_links_to_teach_lineage(tmp_path: Path, monkeypatch) -> None:
    """Memory artifacts captured during teach-reuse land under the teach lineage.

    Simulates the post-Phase-2.2 path: capture_project_memory returns paths;
    `_register_capture_artifacts` is called once per active lineage (turn +
    teach). After both registrations, `ledger.artifacts_for_lineage(teach)`
    includes the memory path so `rollback_lineage(teach)` would move it.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    teach_lineage_id = new_lineage_id("teach")
    policy_id = "pnpm-preference-correction"
    runtime.ledger.append(_make_teach_record(policy_id, teach_lineage_id))

    memory_path = workspace / ".rocky" / "memories" / "auto" / "pref-42.json"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text('{"id": "pref-42", "kind": "preference"}\n', encoding="utf-8")

    turn_lineage_id = new_lineage_id("turn")
    capture_result = {
        "written": 1,
        "candidates": [],
        "notes": [{"path": str(memory_path)}],
    }

    runtime._register_capture_artifacts(turn_lineage_id, capture_result)
    for teach in runtime._active_teach_lineages({"selected_policies": [policy_id]}):
        if teach and teach != turn_lineage_id:
            runtime._register_capture_artifacts(teach, capture_result)

    turn_paths = [str(p) for p in runtime.ledger.artifacts_for_lineage(turn_lineage_id)]
    teach_paths = [str(p) for p in runtime.ledger.artifacts_for_lineage(teach_lineage_id)]

    assert str(memory_path) in turn_paths, (
        f"Memory artifact must be registered under the turn lineage; got {turn_paths!r}."
    )
    assert str(memory_path) in teach_paths, (
        f"Memory artifact must ALSO be registered under the active teach lineage "
        f"so /undo on the teach lineage sweeps it; got {teach_paths!r}. "
        f"This is the derived-autonomous leak fix."
    )


def test_non_teach_turn_does_not_link_to_any_teach_lineage(
    tmp_path: Path, monkeypatch
) -> None:
    """CF-4 preservation: a capture during a plain autonomous turn must NOT
    link to any teach lineage — only the turn lineage. Otherwise we'd corrupt
    the autonomous pathway with false teach attribution.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    # Seed a teach record that exists but is NOT being reused this turn.
    teach_lineage_id = new_lineage_id("teach")
    policy_id = "pnpm-preference-correction"
    runtime.ledger.append(_make_teach_record(policy_id, teach_lineage_id))

    # Trace has no selected_policies — autonomous capture, no teach reuse.
    resolved = runtime._active_teach_lineages({"selected_policies": []})
    assert resolved == [], (
        f"A turn with no reused policies must not surface any teach lineage. "
        f"Got {resolved!r}. CF-4 violation would corrupt autonomous pathway."
    )
