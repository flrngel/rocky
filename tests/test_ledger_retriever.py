"""T2 — Unified ledger retriever + 10-factor ranking (Phase 2.3).

Deterministic tests for `LedgerRetriever.retrieve` — the new ledger-backed
retrieval path with PRD §12.3 10-factor scoring. Does NOT replace existing
retrievers in Phase 2.3 (T3 adapter collapse is deferred).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.learning.ledger import LearningRecord, new_lineage_id
from rocky.learning.ledger_retriever import LedgerRetriever, RankedRecord
from rocky.util.time import utc_iso


def _make_record(
    *,
    record_id: str,
    kind: str = "procedure",
    authority: str = "teacher",
    promotion_state: str = "promoted",
    task_signature: str = "repo/shell_execution",
    task_family: str = "repo",
    failure_class: str | None = None,
    triggers: list[str] | None = None,
    required_behavior: list[str] | None = None,
    evidence: list[str] | None = None,
    reuse_stats: dict[str, int] | None = None,
    rolled_back: bool = False,
) -> LearningRecord:
    stamp = utc_iso()
    return LearningRecord(
        id=record_id,
        kind=kind,
        scope="project",
        authority=authority,
        promotion_state=promotion_state,
        activation_mode="soft",
        task_signature=task_signature,
        task_family=task_family,
        failure_class=failure_class,
        triggers=triggers or [],
        required_behavior=required_behavior or [],
        prohibited_behavior=[],
        evidence=evidence or [],
        lineage={"id": record_id},
        created_at=stamp,
        updated_at=stamp,
        origin={"type": "teacher_feedback"},
        reuse_stats=reuse_stats or {},
        rolled_back=rolled_back,
    )


def _runtime(tmp_path: Path, monkeypatch) -> RockyRuntime:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return RockyRuntime.load_from(workspace)


def test_retrieve_requires_some_signal(tmp_path: Path, monkeypatch) -> None:
    """Records with no trigger/signature/thread/prompt signal are skipped."""
    runtime = _runtime(tmp_path, monkeypatch)
    runtime.ledger.append(
        _make_record(
            record_id="rec-1",
            triggers=["something-unrelated"],
            task_signature="automation/general",
        )
    )
    retriever = LedgerRetriever(runtime.ledger)
    results = retriever.retrieve("install a dependency", "repo/shell_execution")
    assert results == [], "Record with zero signal must not be returned."


def test_trigger_literal_match_surfaces_record(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    runtime.ledger.append(
        _make_record(
            record_id="rec-trigger",
            triggers=["install", "dependency"],
            task_signature="repo/shell_execution",
        )
    )
    retriever = LedgerRetriever(runtime.ledger)
    results = retriever.retrieve("install a dependency", "repo/shell_execution")
    assert len(results) == 1
    ranked = results[0]
    assert ranked.record.id == "rec-trigger"
    assert ranked.rank_breakdown["trigger_literal"] == 6.0
    assert ranked.rank_breakdown["task_signature"] == 6.0


def test_ranking_exposes_all_prd_factors(tmp_path: Path, monkeypatch) -> None:
    """Every record must expose the 10-factor breakdown (PRD §12.3)."""
    runtime = _runtime(tmp_path, monkeypatch)
    runtime.ledger.append(
        _make_record(
            record_id="rec-factors",
            triggers=["install"],
            task_signature="repo/shell_execution",
        )
    )
    retriever = LedgerRetriever(runtime.ledger)
    results = retriever.retrieve("install something", "repo/shell_execution")
    assert len(results) == 1
    breakdown = results[0].rank_breakdown
    for factor in (
        "authority",
        "promotion_state",
        "task_signature",
        "task_family",
        "thread_relevance",
        "prompt_relevance",
        "trigger_literal",
        "failure_class",
        "evidence_quality",
        "recency",
        "conflict_status",
        "prior_success",
    ):
        assert factor in breakdown, f"PRD §12.3 factor {factor!r} missing from rank_breakdown"


def test_promoted_outranks_candidate(tmp_path: Path, monkeypatch) -> None:
    """Authority + promotion weights give promoted records higher score."""
    runtime = _runtime(tmp_path, monkeypatch)
    runtime.ledger.append(
        _make_record(
            record_id="promoted",
            triggers=["install"],
            promotion_state="promoted",
            task_signature="repo/shell_execution",
        )
    )
    runtime.ledger.append(
        _make_record(
            record_id="candidate",
            triggers=["install"],
            promotion_state="candidate",
            task_signature="repo/shell_execution",
        )
    )
    retriever = LedgerRetriever(runtime.ledger)
    results = retriever.retrieve("install a dependency", "repo/shell_execution")
    assert len(results) == 2
    # Promoted record must rank first.
    assert results[0].record.id == "promoted"
    assert results[0].score > results[1].score


def test_rolled_back_records_are_excluded(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    runtime.ledger.append(
        _make_record(
            record_id="rec-live",
            triggers=["install"],
            task_signature="repo/shell_execution",
        )
    )
    runtime.ledger.append(
        _make_record(
            record_id="rec-dead",
            triggers=["install"],
            task_signature="repo/shell_execution",
            rolled_back=True,
        )
    )
    retriever = LedgerRetriever(runtime.ledger)
    results = retriever.retrieve("install", "repo/shell_execution")
    ids = [r.record.id for r in results]
    assert "rec-live" in ids
    assert "rec-dead" not in ids, "Rolled-back records must be excluded by T2 retriever."


def test_kind_filter_restricts_results(tmp_path: Path, monkeypatch) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    runtime.ledger.append(
        _make_record(
            record_id="proc",
            kind="procedure",
            triggers=["install"],
            task_signature="repo/shell_execution",
        )
    )
    runtime.ledger.append(
        _make_record(
            record_id="pref",
            kind="preference",
            triggers=["install"],
            task_signature="repo/shell_execution",
        )
    )
    retriever = LedgerRetriever(runtime.ledger)
    results = retriever.retrieve(
        "install",
        "repo/shell_execution",
        kind_filter={"procedure"},
    )
    ids = [r.record.id for r in results]
    assert ids == ["proc"]


def test_verified_success_count_boosts_score(tmp_path: Path, monkeypatch) -> None:
    """`reuse_stats.verified_success_count` is PRD §12.3 factor 10."""
    runtime = _runtime(tmp_path, monkeypatch)
    runtime.ledger.append(
        _make_record(
            record_id="untested",
            triggers=["install"],
            reuse_stats={"verified_success_count": 0},
        )
    )
    runtime.ledger.append(
        _make_record(
            record_id="tested",
            triggers=["install"],
            reuse_stats={"verified_success_count": 3},
        )
    )
    retriever = LedgerRetriever(runtime.ledger)
    results = retriever.retrieve("install", "repo/shell_execution")
    assert results[0].record.id == "tested"
    assert (
        results[0].rank_breakdown["prior_success"]
        > results[1].rank_breakdown["prior_success"]
    )
