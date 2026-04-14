"""T4 — retriever-side rollback filter (Phase 2.2).

Belt-and-suspenders guard: a ledger record whose lineage has been rolled back
must never surface through the context builder even if its file still exists
on disk. The primary defense is `LearningLedgerStore.rollback_lineage()` moving
the file out of the workspace — this test covers the edge where the file is
still present but the lineage is flagged rolled_back.

Deterministic. No live LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.learning.ledger import LearningRecord, new_lineage_id
from rocky.util.time import utc_iso


def _make_memory_record(memory_id: str, lineage_id: str, path: Path) -> LearningRecord:
    stamp = utc_iso()
    return LearningRecord(
        id=lineage_id,
        kind="preference",
        scope="project_auto",
        authority="evidence_backed",
        promotion_state="promoted",
        activation_mode="soft",
        task_signature="",
        task_family="",
        failure_class=None,
        triggers=[],
        required_behavior=[],
        prohibited_behavior=[],
        evidence=[],
        lineage={"id": lineage_id, "memory_id": memory_id, "path": str(path)},
        created_at=stamp,
        updated_at=stamp,
        origin={"type": "autonomous_capture", "path": str(path)},
        reuse_stats={},
    )


def test_is_path_in_rolled_back_lineage_via_index(tmp_path: Path, monkeypatch) -> None:
    """`is_path_in_rolled_back_lineage` returns True for paths registered under
    a rolled-back lineage — even when the file still exists.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    stranded = workspace / ".rocky" / "memories" / "auto" / "stranded.json"
    stranded.parent.mkdir(parents=True, exist_ok=True)
    stranded.write_text('{"id": "stranded", "kind": "preference"}\n', encoding="utf-8")

    lineage_id = new_lineage_id("turn")
    runtime.ledger.append(_make_memory_record("stranded", lineage_id, stranded))
    runtime.ledger.register_artifact(lineage_id, stranded)

    # Not rolled back yet — filter should let it through.
    assert runtime.ledger.is_path_in_rolled_back_lineage(stranded) is False

    runtime.ledger.mark_rolled_back(lineage_id)

    # File still exists on disk; lineage rolled back → filter bites.
    assert stranded.exists(), "fixture invariant: file must still exist for this test"
    assert runtime.ledger.is_path_in_rolled_back_lineage(stranded) is True, (
        f"Rolled-back lineage containing {stranded} must be detected even "
        f"when the artifact file was not moved. Belt-and-suspenders guard for T4."
    )


def test_is_path_in_rolled_back_lineage_ignores_fresh_lineage(
    tmp_path: Path, monkeypatch
) -> None:
    """A path on a DIFFERENT non-rolled-back lineage must still be returned
    (false negatives would cause legitimate artifacts to disappear).
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    live_path = workspace / ".rocky" / "memories" / "auto" / "live.json"
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text('{"id": "live"}\n', encoding="utf-8")

    live_lineage = new_lineage_id("turn")
    runtime.ledger.append(_make_memory_record("live", live_lineage, live_path))
    runtime.ledger.register_artifact(live_lineage, live_path)

    assert runtime.ledger.is_path_in_rolled_back_lineage(live_path) is False

    # Roll back a DIFFERENT lineage (unrelated).
    other_lineage = new_lineage_id("turn")
    runtime.ledger.append(_make_memory_record("other", other_lineage, workspace / "other"))
    runtime.ledger.mark_rolled_back(other_lineage)

    # live_path must still be allowed through — the rolled-back lineage
    # doesn't contain it.
    assert runtime.ledger.is_path_in_rolled_back_lineage(live_path) is False, (
        "An unrelated rolled-back lineage must NOT taint paths from other lineages."
    )


def test_context_builder_filter_helper_returns_true_for_rolled_back_path(
    tmp_path: Path, monkeypatch
) -> None:
    """ContextBuilder._is_artifact_rolled_back delegates correctly to the ledger.

    This is a unit-level proof that the builder's filter hook wires the ledger
    check into the build path — avoiding a full MemoryNote schema dance for
    deterministic verification.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = RockyRuntime.load_from(workspace)

    stranded = workspace / ".rocky" / "memories" / "auto" / "stranded.json"
    stranded.parent.mkdir(parents=True, exist_ok=True)
    stranded.write_text("{}\n", encoding="utf-8")

    lineage_id = new_lineage_id("turn")
    runtime.ledger.append(_make_memory_record("stranded", lineage_id, stranded))
    runtime.ledger.register_artifact(lineage_id, stranded)

    assert runtime.context_builder._is_artifact_rolled_back(stranded) is False
    runtime.ledger.mark_rolled_back(lineage_id)
    assert runtime.context_builder._is_artifact_rolled_back(stranded) is True, (
        "ContextBuilder must recognize rolled-back-lineage paths via its ledger "
        "hook. Without this, the retriever-side filter is a no-op."
    )
