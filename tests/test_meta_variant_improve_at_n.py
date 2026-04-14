"""Phase 4 — transfer-evaluation tests (run-20260414-221947).

Covers: CanaryTask.task_family default, corpus shape, per-family aggregation,
improve_at_n math, empty-history guard, cmd_meta CLI dispatch, and a
positive-transfer demonstration.

All tests deterministic — no live-LLM dependency.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.config.models import PackingConfig, RetrievalConfig
from rocky.meta.canary import (
    CanaryRunner,
    CanaryTask,
    default_corpus,
    improve_at_n,
)


# ---------------------------------------------------------------------------
# Schema + corpus
# ---------------------------------------------------------------------------


def test_canary_task_default_task_family() -> None:
    """CF-4: existing callers that don't set `task_family` still work."""
    task = CanaryTask(
        task_id="x",
        prompt="y",
        task_signature="repo/shell_execution",
        seed_records=(),
    )
    assert task.task_family == "repo"


def test_canary_task_explicit_task_family() -> None:
    task = CanaryTask(
        task_id="research",
        prompt="compare",
        task_signature="site/understanding/general",
        seed_records=(),
        task_family="research",
    )
    assert task.task_family == "research"


def test_default_corpus_has_held_out_family() -> None:
    corpus = default_corpus()
    families = {t.task_family for t in corpus.tasks}
    assert "repo" in families
    assert "research" in families
    assert len(corpus.tasks) >= 3


# ---------------------------------------------------------------------------
# Per-family aggregation in CanaryRunner
# ---------------------------------------------------------------------------


def test_canary_runner_emits_per_family_aggregate(tmp_path: Path) -> None:
    runner = CanaryRunner(default_corpus())
    baseline = runner.run("baseline", RetrievalConfig(), PackingConfig(), tmp_path)
    pf = baseline.aggregate["per_family"]
    assert set(pf.keys()) == {"repo", "research"}
    # Every family bucket has the four expected keys
    for family, bucket in pf.items():
        assert set(bucket.keys()) == {
            "total_records_returned",
            "total_packer_chars",
            "task_count",
            "top1_stability_hash",
        }
    # Per-family sum equals top-level
    total_records = sum(v["total_records_returned"] for v in pf.values())
    total_chars = sum(v["total_packer_chars"] for v in pf.values())
    assert total_records == baseline.aggregate["total_records_returned"]
    assert total_chars == baseline.aggregate["total_packer_chars"]


# ---------------------------------------------------------------------------
# improve_at_n math
# ---------------------------------------------------------------------------


def _make_agg(repo_records: int, research_records: int) -> dict:
    return {
        "total_records_returned": repo_records + research_records,
        "total_packer_chars": 100 * (repo_records + research_records),
        "task_count": 2,
        "top1_stability_hash": "fixed",
        "per_family": {
            "repo": {
                "total_records_returned": repo_records,
                "total_packer_chars": 100 * repo_records,
                "task_count": 1,
                "top1_stability_hash": "repo",
            },
            "research": {
                "total_records_returned": research_records,
                "total_packer_chars": 100 * research_records,
                "task_count": 1,
                "top1_stability_hash": "research",
            },
        },
    }


def test_improve_at_n_math_single_run() -> None:
    baseline = _make_agg(repo_records=10, research_records=5)
    result = _make_agg(repo_records=4, research_records=2)
    out = improve_at_n([result], baseline, target_family="repo")
    assert out["n"] == 1
    assert out["target_family"] == "repo"
    assert out["held_out_families"] == ["research"]
    # Same-family total_records: 4 - 10 = -6
    assert out["same_family"]["total_records_returned"]["max_delta"] == -6.0
    assert out["same_family"]["total_records_returned"]["mean_delta"] == -6.0
    assert out["same_family"]["total_records_returned"]["deltas"] == [-6.0]
    # Held-out total_records: 2 - 5 = -3
    assert out["held_out_family"]["total_records_returned"]["max_delta"] == -3.0


def test_improve_at_n_math_multi_run_max_and_mean() -> None:
    """max_delta picks the delta with largest absolute value; mean averages all deltas."""
    baseline = _make_agg(repo_records=10, research_records=5)
    r1 = _make_agg(repo_records=4, research_records=5)   # -6 repo, 0 research
    r2 = _make_agg(repo_records=8, research_records=5)   # -2 repo, 0 research
    out = improve_at_n([r1, r2], baseline, target_family="repo")
    assert out["n"] == 2
    deltas = out["same_family"]["total_records_returned"]["deltas"]
    assert deltas == [-6.0, -2.0]
    assert out["same_family"]["total_records_returned"]["max_delta"] == -6.0  # largest abs
    assert out["same_family"]["total_records_returned"]["mean_delta"] == -4.0  # (-6 + -2)/2


def test_improve_at_n_empty_history_returns_error_shape() -> None:
    baseline = _make_agg(repo_records=10, research_records=5)
    out = improve_at_n([], baseline)
    assert out["n"] == 0
    assert out["error"] == "no canary history"
    assert out["same_family"] == {}
    assert out["held_out_family"] == {}


def test_improve_at_n_target_family_switches_split() -> None:
    """If target_family=research, the repo family becomes the held-out side."""
    baseline = _make_agg(repo_records=10, research_records=5)
    result = _make_agg(repo_records=4, research_records=2)
    out = improve_at_n([result], baseline, target_family="research")
    assert out["target_family"] == "research"
    assert out["held_out_families"] == ["repo"]
    assert out["same_family"]["total_records_returned"]["deltas"] == [-3.0]  # research 2-5
    assert out["held_out_family"]["total_records_returned"]["deltas"] == [-6.0]  # repo 4-10


# ---------------------------------------------------------------------------
# Positive-transfer demonstration (A5 acceptance)
# ---------------------------------------------------------------------------


def test_positive_transfer_demo_topk_variant(tmp_path: Path) -> None:
    """`retrieval.top_k_limit=2` variant must produce non-zero deltas in BOTH
    same-family AND held-out-family — demonstrating the transfer-evaluation
    calculator observes the variant's effect across families.
    """
    runner = CanaryRunner(default_corpus())
    baseline = runner.run(
        "baseline", RetrievalConfig(), PackingConfig(), tmp_path / "baseline"
    )
    variant_run = runner.run(
        "v-topk", RetrievalConfig(top_k_limit=2), PackingConfig(), tmp_path / "variant"
    )
    out = improve_at_n([variant_run.aggregate], baseline.aggregate, target_family="repo")
    # Same-family (repo): variant narrows 6+6=12 records to 2+2=4 → -8 delta.
    assert out["same_family"]["total_records_returned"]["max_delta"] != 0
    # Held-out (research): variant also narrows 5→2 → -3 delta.
    assert out["held_out_family"]["total_records_returned"]["max_delta"] != 0


# ---------------------------------------------------------------------------
# cmd_meta improve_at_n dispatch
# ---------------------------------------------------------------------------


def test_cmd_meta_improve_at_n_missing_variant(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    r = runtime.commands.handle("/meta improve_at_n never-created")
    assert r.data.get("ok") is False
    assert "not found" in r.data["reason"]


def test_cmd_meta_improve_at_n_no_canary_history(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.commands.handle(
        """/meta create v-empty baseline '{"retrieval.top_k_limit": 2}'"""
    )
    r = runtime.commands.handle("/meta improve_at_n v-empty")
    assert r.data.get("ok") is False
    assert "no canary history" in r.data["reason"]


def test_cmd_meta_improve_at_n_happy_path(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.commands.handle(
        """/meta create v-topk baseline '{"retrieval.top_k_limit": 2}'"""
    )
    runtime.commands.handle("/meta canary v-topk")
    r = runtime.commands.handle("/meta improve_at_n v-topk")
    assert r.data["n"] == 1
    assert r.data["target_family"] == "repo"
    assert "research" in r.data["held_out_families"]
    assert r.data["same_family"]["total_records_returned"]["max_delta"] < 0
    assert r.data["held_out_family"]["total_records_returned"]["max_delta"] < 0


def test_cmd_meta_improve_at_n_target_family_arg(tmp_path: Path) -> None:
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.commands.handle(
        """/meta create v-topk baseline '{"retrieval.top_k_limit": 2}'"""
    )
    runtime.commands.handle("/meta canary v-topk")
    r = runtime.commands.handle("/meta improve_at_n v-topk research")
    assert r.data["target_family"] == "research"
    assert r.data["held_out_families"] == ["repo"]
