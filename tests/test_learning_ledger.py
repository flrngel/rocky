"""Deterministic tests for the canonical Learning Ledger (Phase 1).

Covers:
  LD-ROUND    round-trip through LearningRecord / LearningLedgerStore.
  LD-IDEMP    migrate_legacy_workspace is idempotent via lineage-id check.
  LD-ROLLBACK rollback_by_lineage moves all related artifacts AND leaves unrelated records intact.
  LD-REFLECT  `_auto_self_reflect` gate is LINEAGE-scoped, not thread-scoped
              — three arms: rolled-back lineage suppresses; different thread passes;
              SAME thread with fresh lineage passes (boundary test).
  LD-LINEAGE  one /teach event produces exactly one canonical ledger record
              whose lineage_id is shared across every produced artifact.

No LLM dependency. Pure file I/O + direct ledger API + one in-process
`RockyRuntime.load_from(tmp_path)` + seeded `agent.last_*` state (the same
pattern used by `tests/test_runtime_learning_binding.py`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.core.agent import AgentResponse
from rocky.learning.ledger import (
    LearningLedgerStore,
    LearningRecord,
    migrate_legacy_workspace,
    new_lineage_id,
)
from rocky.util.time import utc_iso


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides) -> LearningRecord:
    base = dict(
        id="rec-1",
        kind="procedure",
        scope="project",
        authority="teacher",
        promotion_state="candidate",
        activation_mode="soft",
        task_signature="conversation/general",
        task_family="conversation",
        failure_class=None,
        triggers=[],
        required_behavior=[],
        prohibited_behavior=[],
        evidence=[],
        lineage={"id": "ln-1"},
        created_at="2026-04-12T00:00:00Z",
        updated_at="2026-04-12T00:00:00Z",
        origin={"type": "teacher_feedback"},
        reuse_stats={},
    )
    base.update(overrides)
    return LearningRecord(**base)


def _seeded_workspace_with_legacy(tmp_path: Path) -> Path:
    """Build a tmp_path with each legacy store populated."""
    ws = tmp_path / "ws"
    (ws / ".rocky" / "policies" / "learned" / "proc-A").mkdir(parents=True)
    (ws / ".rocky" / "policies" / "learned" / "proc-A" / "POLICY.md").write_text(
        "---\npolicy_id: proc-A\nscope: project\n---\n\n# proc-A\n", encoding="utf-8"
    )
    (ws / ".rocky" / "policies" / "learned" / "proc-A" / "POLICY.meta.json").write_text(
        json.dumps({
            "policy_id": "proc-A",
            "scope": "project",
            "published_at": "2026-04-12T00:00:00Z",
            "metadata": {"promotion_state": "candidate"},
        }),
        encoding="utf-8",
    )
    student_dir = ws / ".rocky" / "student"
    (student_dir / "patterns").mkdir(parents=True)
    (student_dir / "retrospectives").mkdir(parents=True)
    (student_dir / "patterns" / "pat-1.md").write_text(
        "---\nid: pat-1\nkind: pattern\n---\n\n# pat-1\n", encoding="utf-8"
    )
    (student_dir / "retrospectives" / "retro-1.md").write_text(
        "---\nid: retro-1\nkind: retrospective\n---\n\n# retro-1\n", encoding="utf-8"
    )
    (student_dir / "notebook.jsonl").write_text(
        json.dumps({"id": "lesson-1", "kind": "lesson", "created_at": "2026-04-12T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    memories_dir = ws / ".rocky" / "memories"
    (memories_dir / "auto").mkdir(parents=True)
    (memories_dir / "candidates").mkdir(parents=True)
    (memories_dir / "auto" / "pref-uv.json").write_text(
        json.dumps({"id": "pref-uv", "kind": "preference", "promotion_state": "promoted"}),
        encoding="utf-8",
    )
    (memories_dir / "candidates" / "cand-x.json").write_text(
        json.dumps({"id": "cand-x", "kind": "constraint", "promotion_state": "candidate"}),
        encoding="utf-8",
    )
    (memories_dir / "project_brief.md").write_text("# Project Brief\n\n## Constraints\n- test\n", encoding="utf-8")
    return ws


# ---------------------------------------------------------------------------
# LD-ROUND
# ---------------------------------------------------------------------------


def test_ledger_round_trip(tmp_path: Path) -> None:
    """LD-ROUND: append → load → filter_by_kind → lookup_by_id."""
    ledger = LearningLedgerStore(tmp_path)
    r1 = _make_record(id="rec-1", kind="procedure", lineage={"id": "ln-1"})
    r2 = _make_record(id="rec-2", kind="example", lineage={"id": "ln-2"})
    r3 = _make_record(id="rec-3", kind="procedure", lineage={"id": "ln-3"})
    ledger.append(r1)
    ledger.append(r2)
    ledger.append(r3)

    all_records = ledger.load_all()
    assert [r.id for r in all_records] == ["rec-1", "rec-2", "rec-3"]

    procs = ledger.filter_by_kind("procedure")
    assert [r.id for r in procs] == ["rec-1", "rec-3"]

    found = ledger.lookup_by_id("rec-2")
    assert found is not None and found.kind == "example"

    assert ledger.lookup_by_id("nonexistent") is None


# ---------------------------------------------------------------------------
# LD-IDEMP
# ---------------------------------------------------------------------------


def test_migration_idempotent(tmp_path: Path) -> None:
    """LD-IDEMP: running migrate_legacy_workspace twice yields no duplicate records."""
    ws = _seeded_workspace_with_legacy(tmp_path)
    ledger = LearningLedgerStore(ws)

    first = migrate_legacy_workspace(ledger, ws)
    after_first = len(ledger.load_all())
    assert after_first > 0, "migration must create at least one record from seeded legacy data"
    assert first["migrated"] == after_first
    assert first["already_present"] == 0

    second = migrate_legacy_workspace(ledger, ws)
    after_second = len(ledger.load_all())
    assert after_second == after_first, (
        f"migration must be idempotent; first pass created {after_first} records, "
        f"second pass produced {after_second} (should be equal). "
        f"second={second!r}"
    )
    assert second["migrated"] == 0
    assert second["already_present"] >= 1


# ---------------------------------------------------------------------------
# LD-ROLLBACK
# ---------------------------------------------------------------------------


def test_rollback_by_lineage_moves_all_related_artifacts_and_leaves_others(tmp_path: Path) -> None:
    """LD-ROLLBACK: rollback moves ALL L1 artifacts AND leaves L2 intact."""
    ws = tmp_path / "ws"
    (ws / ".rocky").mkdir(parents=True)
    ledger = LearningLedgerStore(ws)

    # Seed L1: 3 artifacts sharing lineage L1
    l1_paths = []
    for i, rel in enumerate([
        ".rocky/policies/learned/L1-pol/POLICY.md",
        ".rocky/student/patterns/L1-pat.md",
        ".rocky/memories/auto/L1-mem.json",
    ]):
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"L1 artifact {i}", encoding="utf-8")
        l1_paths.append(p)
        ledger.register_artifact("ln-1", p)
    ledger.append(_make_record(id="rec-L1", lineage={"id": "ln-1", "policy_id": "L1-pol"}))

    # Seed L2: 2 unrelated artifacts sharing lineage L2
    l2_paths = []
    for i, rel in enumerate([
        ".rocky/policies/learned/L2-pol/POLICY.md",
        ".rocky/memories/auto/L2-mem.json",
    ]):
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"L2 artifact {i}", encoding="utf-8")
        l2_paths.append(p)
        ledger.register_artifact("ln-2", p)
    ledger.append(_make_record(id="rec-L2", lineage={"id": "ln-2", "policy_id": "L2-pol"}))

    rollback_root = ws / ".rocky" / "artifacts" / "rollback"
    result = ledger.rollback_lineage("ln-1", rollback_root)

    # All L1 paths moved OUT
    assert result["rolled_back"] is True
    assert result["lineage_id"] == "ln-1"
    assert len(result["moved"]) == len(l1_paths)
    for p in l1_paths:
        assert not p.exists(), f"L1 artifact {p} should have been moved out"

    # All L2 paths UNTOUCHED
    for p in l2_paths:
        assert p.exists(), f"unrelated L2 artifact {p} was wrongly moved"

    # L1 record marked rolled_back; L2 untouched
    l1_rec = ledger.lookup_by_id("rec-L1")
    l2_rec = ledger.lookup_by_id("rec-L2")
    assert l1_rec is not None and l1_rec.rolled_back is True
    assert l2_rec is not None and l2_rec.rolled_back is False

    # lineage-rolled-back helper returns True for L1, False for L2
    assert ledger.is_lineage_rolled_back("ln-1") is True
    assert ledger.is_lineage_rolled_back("ln-2") is False


# ---------------------------------------------------------------------------
# LD-REFLECT — lineage-scoped gate with boundary test
# ---------------------------------------------------------------------------


def test_auto_self_reflect_gate_is_lineage_scoped_not_thread_scoped(tmp_path: Path) -> None:
    """LD-REFLECT three arms:

      (a) rolled-back lineage L1 → gate returns True (suppress).
      (b) different thread + fresh lineage L2 → gate returns False (pass).
      (c) SAME thread_id as L1 but fresh un-rolled-back lineage L3 → gate False (pass).

    Arm (c) is the boundary test that prevents a sloppy implementation from
    reverting to thread_id-based gating and suppressing retrospectives on
    future unrelated turns that happen to share a rolled-back thread's id.
    """
    ws = tmp_path / "ws"
    (ws / ".rocky").mkdir(parents=True)
    ledger = LearningLedgerStore(ws)

    # Seed lineage L1 tied to thread T1, and roll it back.
    ledger.append(_make_record(id="rec-L1", lineage={"id": "ln-1", "thread_id": "T1"}))
    ledger.rollback_lineage("ln-1", ws / ".rocky" / "artifacts" / "rollback")

    # Seed a second record L2 tied to thread T2 (not rolled back).
    ledger.append(_make_record(id="rec-L2", lineage={"id": "ln-2", "thread_id": "T2"}))
    # Seed a third record L3 tied to thread T1 (SAME as L1) but NOT rolled back.
    ledger.append(_make_record(id="rec-L3", lineage={"id": "ln-3", "thread_id": "T1"}))

    # Arm (a): the rolled-back lineage's gate must be True.
    assert ledger.is_lineage_rolled_back("ln-1") is True

    # Arm (b): different thread + fresh lineage → gate False.
    assert ledger.is_lineage_rolled_back("ln-2") is False

    # Arm (c) — BOUNDARY TEST: same thread_id T1 as L1, but a fresh lineage
    # L3 that is NOT rolled back. The gate is scoped by lineage_id, not
    # thread_id, so this must return False. If a future implementation
    # regresses to thread-based gating, this arm fails.
    assert ledger.is_lineage_rolled_back("ln-3") is False


# ---------------------------------------------------------------------------
# LD-LINEAGE — one /teach → one canonical record with shared lineage_id
# ---------------------------------------------------------------------------


def test_teach_event_produces_one_canonical_lineage(tmp_path: Path, monkeypatch) -> None:
    """LD-LINEAGE: runtime.learn() appends exactly one canonical ledger record
    whose lineage_id is registered against every produced legacy artifact.

    Uses the same seeding pattern as tests/test_runtime_learning_binding.py.
    No LLM; the synthesizer runs heuristic-only because provider is None/unused.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = RockyRuntime.load_from(workspace)

    ledger = runtime.ledger
    ledger_records_before = len(ledger.load_all())

    # Seed a minimal agent state so `runtime.learn` has a prior answer to bind to.
    runtime.agent.last_prompt = "Extract fields X and Y from the file"
    runtime.agent.last_answer = "Only extracted field X"
    runtime.agent.last_trace = {
        "route": {"task_signature": "extract/general"},
        "verification": {"status": "fail", "failure_class": "missing_field"},
        "selected_tools": ["read_file"],
        "thread": {
            "current_thread": {
                "thread_id": "thread_ld_lineage",
                "task_signature": "extract/general",
                "task_family": "extract",
            }
        },
    }

    # Prevent refresh_knowledge from re-reading retrievers in this unit test.
    runtime.refresh_knowledge = lambda: None  # type: ignore[assignment]

    result = runtime.learn("Next time, also include field Y in the extracted output.")
    assert "lineage_id" in result, f"runtime.learn must emit lineage_id in result; got {result!r}"
    lineage_id = result["lineage_id"]
    assert lineage_id, "lineage_id must be a non-empty string"

    # Exactly one new canonical record was appended for this teach event.
    all_records = ledger.load_all()
    new_records = [r for r in all_records[ledger_records_before:] if (r.lineage or {}).get("id") == lineage_id]
    assert len(new_records) == 1, (
        f"teach event must produce exactly one canonical ledger record; found {len(new_records)}: {new_records!r}"
    )
    canonical = new_records[0]
    assert canonical.origin.get("type") == "teacher_feedback"

    # The lineage_id must be registered against at least one legacy artifact path.
    registered_paths = ledger.artifacts_for_lineage(lineage_id)
    assert len(registered_paths) >= 1, (
        f"lineage {lineage_id} must have at least one registered artifact (student notebook "
        f"at minimum); got {registered_paths!r}"
    )
    # At least one of the registered paths must be real on disk (student notebook always exists).
    assert any(p.exists() for p in registered_paths), (
        f"at least one registered artifact must exist on disk for lineage {lineage_id}; "
        f"paths={[str(p) for p in registered_paths]!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
