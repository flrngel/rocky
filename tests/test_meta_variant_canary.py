"""Phase 3 T-META-5 — offline deterministic canary.

Covers SC-OVERLAY-DELTA (A3, A8, A9):
  * Default corpus produces stable metrics across runs (determinism).
  * Baseline canary yields ≥5 records on the top-K task (so a narrow variant
    has room to show a measurable delta).
  * `v-topk-narrow` (retrieval.top_k_limit=2) produces a strictly-smaller
    `total_records_returned` than baseline — the delta is ≥ 3.
  * `v-procedural-tighter` (packing.procedural_cap=2) produces a strictly-
    smaller `total_packer_chars` than baseline.
  * Zero-edit variant's metrics equal baseline metrics (A9 sensitivity).
  * No LLM calls (offline gate).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rocky.config.models import PackingConfig, RetrievalConfig
from rocky.meta.canary import CanaryRunner, default_corpus


def test_corpus_is_deterministic(tmp_path: Path) -> None:
    corpus = default_corpus()
    runner = CanaryRunner(corpus)
    a = runner.run("baseline", RetrievalConfig(), PackingConfig(), tmp_path / "a")
    b = runner.run("baseline", RetrievalConfig(), PackingConfig(), tmp_path / "b")
    assert a.aggregate == b.aggregate
    assert a.per_task == b.per_task


def test_baseline_retrieves_enough_records_for_narrowing(tmp_path: Path) -> None:
    runner = CanaryRunner(default_corpus())
    baseline = runner.run(
        "baseline", RetrievalConfig(), PackingConfig(), tmp_path / "baseline"
    )
    topk_task = next(p for p in baseline.per_task if p["task_id"] == "ledger_topk")
    assert topk_task["top_k_record_count"] >= 5, (
        "baseline must return ≥5 records for ledger_topk so narrowing to 2 is observable"
    )


def test_topk_narrowing_produces_measurable_delta(tmp_path: Path) -> None:
    runner = CanaryRunner(default_corpus())
    baseline = runner.run(
        "baseline", RetrievalConfig(), PackingConfig(), tmp_path / "baseline"
    )
    narrow = runner.run(
        "v-topk-narrow",
        RetrievalConfig(top_k_limit=2),
        PackingConfig(),
        tmp_path / "v-topk",
    )
    assert (
        narrow.aggregate["total_records_returned"]
        < baseline.aggregate["total_records_returned"]
    )
    # Delta must be at least 3 on the default corpus (ledger_topk goes 6 → 2 alone).
    delta = (
        baseline.aggregate["total_records_returned"]
        - narrow.aggregate["total_records_returned"]
    )
    assert delta >= 3
    # Every per-task top_k_record_count is ≤ 2 under the narrowed variant.
    for task in narrow.per_task:
        assert task["top_k_record_count"] <= 2


def test_procedural_tighter_produces_smaller_packer_output(tmp_path: Path) -> None:
    runner = CanaryRunner(default_corpus())
    baseline = runner.run(
        "baseline", RetrievalConfig(), PackingConfig(), tmp_path / "baseline"
    )
    tighter = runner.run(
        "v-procedural-tighter",
        RetrievalConfig(),
        PackingConfig(procedural_cap=2),
        tmp_path / "v-proc",
    )
    assert (
        tighter.aggregate["total_packer_chars"]
        < baseline.aggregate["total_packer_chars"]
    )


def test_zero_edit_variant_matches_baseline_exactly(tmp_path: Path) -> None:
    """A9 sensitivity — reverting the edit must erase the canary delta."""
    runner = CanaryRunner(default_corpus())
    baseline = runner.run(
        "baseline", RetrievalConfig(), PackingConfig(), tmp_path / "baseline"
    )
    zero_edit = runner.run(
        "v-zero-edit",  # same default configs as baseline
        RetrievalConfig(),
        PackingConfig(),
        tmp_path / "v-zero-edit",
    )
    assert zero_edit.aggregate == baseline.aggregate
    assert (
        zero_edit.aggregate["top1_stability_hash"]
        == baseline.aggregate["top1_stability_hash"]
    )


def test_result_is_json_serializable(tmp_path: Path) -> None:
    runner = CanaryRunner(default_corpus())
    result = runner.run(
        "baseline", RetrievalConfig(), PackingConfig(), tmp_path / "dump"
    )
    # Must be JSON-serializable for meta-ledger persistence and cmd_meta output.
    json.dumps(result.to_dict(), sort_keys=True)
