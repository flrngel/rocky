"""Phase 3 T3 (limit-narrowed) — meta-variant top_k_limit reaches live retrievers.

Covers SC-T3-LIMIT-REACH, SC-T3-EXPLICIT-LIMIT-PRESERVED, SC-T3-NO-VARIANT-NO-CHANGE,
SC-T3-RUNTIME-INTEGRATION (A3 + A4 from spec.md).

Honest scope note: this run wires the meta-variant `top_k_limit` overlay through
to all three legacy retrievers (`LearnedPolicyRetriever`, `MemoryRetriever`,
`StudentStore`). Ranking weights in `RetrievalConfig` (`authority_weight`,
`promotion_weight`, etc.) remain canary-only because the legacy retrievers have
their own scoring shape that can't be safely unified without a behavioral
rebaseline. A future "T3-Deep" run would do that with full live-test coverage.

Sensitivity: removing the `config=` plumbing in any of the three retrievers
(or in `RockyRuntime.load_from`) flips the corresponding test to RED.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rocky.app import RockyRuntime
from rocky.config.models import RetrievalConfig
from rocky.learning.policies import LearnedPolicy, LearnedPolicyRetriever
from rocky.memory.retriever import MemoryRetriever
from rocky.memory.store import MemoryNote
from rocky.student.store import StudentStore


# ---------------------------------------------------------------------------
# LearnedPolicyRetriever
# ---------------------------------------------------------------------------


def _policy(idx: int) -> LearnedPolicy:
    return LearnedPolicy(
        policy_id=f"pol-{idx}",
        scope="project",
        path=Path(f"/tmp/policies/pol-{idx}/POLICY.md"),
        body=f"# Install dependency policy {idx}",
        metadata={
            "policy_id": f"pol-{idx}",
            "task_signatures": ["repo/shell_execution"],
            "promotion_state": "promoted",
            "retrieval": {"triggers": ["install dependency"], "keywords": ["install"]},
            "description": "install a dependency",
        },
        origin="learned",
        storage_format="policy",
    )


def test_policy_overlay_caps_top_k_when_no_explicit_limit() -> None:
    policies = [_policy(i) for i in range(6)]
    retriever = LearnedPolicyRetriever(policies, config=RetrievalConfig(top_k_limit=2))
    results = retriever.retrieve("install a dependency", "repo/shell_execution")
    assert len(results) == 2


def test_policy_explicit_limit_overrides_overlay() -> None:
    """SC-T3-EXPLICIT-LIMIT-PRESERVED — `agent.py:238` passes limit=6 deliberately."""
    policies = [_policy(i) for i in range(6)]
    retriever = LearnedPolicyRetriever(policies, config=RetrievalConfig(top_k_limit=2))
    results = retriever.retrieve(
        "install a dependency", "repo/shell_execution", limit=6
    )
    assert len(results) == 6


def test_policy_no_config_preserves_legacy_default() -> None:
    """CF-4 baseline parity — legacy default 4 when no config passed."""
    policies = [_policy(i) for i in range(6)]
    retriever = LearnedPolicyRetriever(policies)  # no config
    results = retriever.retrieve("install a dependency", "repo/shell_execution")
    assert len(results) == 4


# ---------------------------------------------------------------------------
# MemoryRetriever
# ---------------------------------------------------------------------------


def _memory_note(idx: int) -> MemoryNote:
    return MemoryNote(
        id=f"mem-{idx}",
        name=f"mem-{idx}",
        title="install preference",
        scope="project_auto",
        origin="user",
        kind="preference",
        text="install dependencies with pnpm",
        created_at="2026-04-14T00:00:00Z",
        updated_at="2026-04-14T00:00:00Z",
        source_task_signature="repo/shell_execution",
        evidence_excerpt="",
        fingerprint=f"fp-{idx}",
        path=Path(f"/tmp/memories/mem-{idx}.json"),
        writable=False,
        provenance_type="user_asserted",
        contradiction_state="active",
    )


def test_memory_overlay_caps_top_k_when_no_explicit_limit() -> None:
    notes = [_memory_note(i) for i in range(6)]
    retriever = MemoryRetriever(notes, config=RetrievalConfig(top_k_limit=2))
    results = retriever.retrieve("install dependencies in this repo")
    assert len(results) == 2


def test_memory_no_config_preserves_legacy_default() -> None:
    notes = [_memory_note(i) for i in range(6)]
    retriever = MemoryRetriever(notes)
    results = retriever.retrieve("install dependencies in this repo")
    assert len(results) == 4


# ---------------------------------------------------------------------------
# StudentStore
# ---------------------------------------------------------------------------


def _seed_student_notes(store: StudentStore, n: int) -> None:
    for i in range(n):
        store.add(
            kind="lesson",
            title=f"install lesson {i}",
            text="prefer pnpm over npm for installs",
            prompt="how to install",
            answer="use pnpm",
            feedback="prefer pnpm",
            task_signature="repo/shell_execution",
        )


def test_student_overlay_caps_top_k_when_no_explicit_limit(tmp_path: Path) -> None:
    store = StudentStore(tmp_path / "student", config=RetrievalConfig(top_k_limit=2))
    _seed_student_notes(store, 6)
    results = store.retrieve("install dependency", task_signature="repo/shell_execution")
    assert len(results) == 2


def test_student_no_config_preserves_legacy_default(tmp_path: Path) -> None:
    store = StudentStore(tmp_path / "student")
    _seed_student_notes(store, 6)
    results = store.retrieve("install dependency", task_signature="repo/shell_execution")
    assert len(results) == 5


# ---------------------------------------------------------------------------
# Runtime integration — overlay flows through RockyRuntime.load_from
# ---------------------------------------------------------------------------


def _seed_workspace_with_policies(workspace: Path, n: int) -> None:
    learned_root = workspace / ".rocky" / "policies" / "learned"
    for i in range(n):
        pol_dir = learned_root / f"reach-{i}"
        pol_dir.mkdir(parents=True, exist_ok=True)
        (pol_dir / "POLICY.md").write_text(
            "---\n"
            f"policy_id: reach-{i}\n"
            "task_signatures: [repo/shell_execution]\n"
            "promotion_state: promoted\n"
            "retrieval:\n"
            "  triggers: [install dependency]\n"
            "  keywords: [install]\n"
            "description: install a dependency\n"
            "---\n\n"
            f"# Install policy {i}\n",
            encoding="utf-8",
        )


def test_runtime_overlay_reaches_policy_retriever(tmp_path: Path) -> None:
    """SC-T3-RUNTIME-INTEGRATION — activate a top_k_limit=2 variant; live policy retriever caps at 2."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_workspace_with_policies(workspace, 6)

    runtime = RockyRuntime.load_from(workspace)
    # Pre-activation: legacy default 4 (no overlay).
    pre = runtime.policy_retriever.retrieve(
        "install a dependency", "repo/shell_execution"
    )
    assert len(pre) == 4

    runtime.meta_registry.create_variant(
        "v-livereach", {"retrieval.top_k_limit": 2}
    )
    runtime.meta_registry.canary("v-livereach")
    runtime.meta_registry.activate("v-livereach")

    # Re-build runtime so the overlay flows through to a fresh policy retriever
    # (the registry is constructed once at load_from; activating mid-run requires
    # a runtime restart per the Phase 3 plan note R-residual-3).
    runtime2 = RockyRuntime.load_from(workspace)
    assert runtime2.meta_registry.active_id() == "v-livereach"
    post = runtime2.policy_retriever.retrieve(
        "install a dependency", "repo/shell_execution"
    )
    assert len(post) == 2

    # Cleanup: rollback so subsequent tests don't see the overlay.
    runtime2.meta_registry.rollback("v-livereach")
