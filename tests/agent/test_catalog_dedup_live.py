"""CATALOG-DEDUP — memory fingerprint dedup live witness.

Witnesses that ``MemoryStore._existing_auto_note_by_fingerprint``
(`memory/store.py:621`) collapses near-duplicate auto-promoted memories
on disk when the underlying user prompts normalize to the same
fingerprint. The fingerprint is
``hashlib.sha256(f"{kind}:{_clean_text(text).lower()}").hexdigest()``
— so prompts differing only in whitespace or case map to the same
on-disk record, while prompts differing in word choice produce
distinct records.

Pathway exercised end-to-end:
    user prompt (no /teach)
        → AgentCore.run_prompt
        → app._capture_project_memory (gated by verification.status==pass)
        → MemoryStore.capture_project_memory
        → _candidate_from_prompt (stability_score=0.8 for user-asserted
          constraints, exceeds _should_promote threshold 0.7)
        → _upsert_project_auto → _existing_auto_note_by_fingerprint
        → write to ``<workspace>/.rocky/memories/auto/{kind}-{slug}.json``

Positive fixture: 4 prompts that normalize to 2 fingerprints (3 collapse
+ 1 distinct). Asserts <=2 records under .rocky/memories/auto/ for the
relevant kinds.

Bit-flip negative fixture: 4 bytewise-distinct prompts. Asserts >=3
records survive — proves the positive's <=2 is not a vacuous count cap.

Gated by ``ROCKY_LLM_SMOKE=1``. Helpers from
``tests/agent/_helpers.py`` ``__all__`` only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ._helpers import (
    ROCKY_BIN,
    SMOKE_FLAG,
    _install_evidence_finalizer,
    _run_rocky,
)


pytestmark = pytest.mark.skipif(
    os.environ.get(SMOKE_FLAG) != "1",
    reason=(
        f"catalog-dedup live scenario requires {SMOKE_FLAG}=1 "
        f"(real Ollama via editable rocky at {ROCKY_BIN})"
    ),
)


_PROMOTED_KINDS = {"constraint", "preference", "workflow_rule"}

_COLLAPSED_PROMPTS = [
    "I prefer using uv for python installs.",
    "I prefer using   uv for python installs.",
    "I PREFER USING UV FOR PYTHON INSTALLS.",
    "Always default to pnpm for node packages.",
]

_DISTINCT_PROMPTS = [
    "Always use uv for python.",
    "Always use pnpm for node.",
    "Always use cargo for rust.",
    "Always use go modules for golang.",
]


@dataclass
class _CatalogResult:
    turns: list = field(default_factory=list)
    workspace: Path = field(default_factory=Path)


def _count_promotable_records(workspace: Path) -> tuple[int, list[str]]:
    auto_dir = workspace / ".rocky" / "memories" / "auto"
    if not auto_dir.exists():
        return 0, []
    matched: list[str] = []
    for path in sorted(auto_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        kind = str(payload.get("kind") or "")
        if kind in _PROMOTED_KINDS:
            matched.append(path.name)
    return len(matched), matched


@pytest.fixture(scope="module")
def catalog_dedup_collapsed(request, tmp_path_factory) -> _CatalogResult:
    workspace = tmp_path_factory.mktemp("catalog_dedup_collapsed_")
    captures: dict = {}
    _install_evidence_finalizer(request, "catalog_dedup_collapsed", workspace, captures)

    turns = []
    for index, prompt in enumerate(_COLLAPSED_PROMPTS, start=1):
        turn = _run_rocky(
            workspace,
            prompt,
            label=f"t{index}_collapsed",
            captures=captures,
        )
        turns.append(turn)
    return _CatalogResult(turns=turns, workspace=workspace)


@pytest.fixture(scope="module")
def catalog_dedup_distinct(request, tmp_path_factory) -> _CatalogResult:
    workspace = tmp_path_factory.mktemp("catalog_dedup_distinct_")
    captures: dict = {}
    _install_evidence_finalizer(request, "catalog_dedup_distinct", workspace, captures)

    turns = []
    for index, prompt in enumerate(_DISTINCT_PROMPTS, start=1):
        turn = _run_rocky(
            workspace,
            prompt,
            label=f"t{index}_distinct",
            captures=captures,
        )
        turns.append(turn)
    return _CatalogResult(turns=turns, workspace=workspace)


def test_catalog_dedup_phase_A_first_turn_writes_auto_memory(
    catalog_dedup_collapsed: _CatalogResult,
) -> None:
    """Gate: at least one promotable auto-memory must exist after T1.

    If no auto-memory is written at all, the dedup test below is
    vacuously true. Phase A makes the gate explicit.
    """
    count, names = _count_promotable_records(catalog_dedup_collapsed.workspace)
    assert count >= 1, (
        f"CATALOG-DEDUP phase A FAILED: no promotable auto-memory written "
        f"after 4 user-asserted constraint statements. The capture path "
        f"may have been gated off (verification.status != pass) or the "
        f"classifier rejected the kind. names={names!r}"
    )


def test_catalog_dedup_phase_B_count_bounded_by_fingerprints(
    catalog_dedup_collapsed: _CatalogResult,
) -> None:
    """Load-bearing: 4 user prompts that normalize to 2 fingerprints
    must produce <=2 promotable records on disk.

    The first three prompts differ only in whitespace and case; after
    `_clean_text(text).lower()` they share a single fingerprint. The
    fourth prompt is bytewise-distinct (different content). So the
    expected on-disk record count for the {constraint, preference,
    workflow_rule} kinds is at most 2.
    """
    count, names = _count_promotable_records(catalog_dedup_collapsed.workspace)
    assert count <= 2, (
        f"CATALOG-DEDUP phase B FAILED: expected <=2 promotable records "
        f"after 4 prompts that normalize to 2 fingerprints; got {count}. "
        f"This means MemoryStore._existing_auto_note_by_fingerprint did "
        f"NOT collapse the whitespace/case variants — the fingerprint "
        f"function or its caller has regressed. names={names!r}"
    )


def test_catalog_dedup_phase_C_distinct_prompts_do_not_collapse(
    catalog_dedup_distinct: _CatalogResult,
) -> None:
    """Bit-flip negative: 4 bytewise-distinct prompts must produce >=3
    promotable records.

    Proves the phase B count cap is real — distinct fingerprints are
    NOT collapsed; only whitespace/case variants are. Without this
    test, phase B passes vacuously if the dedup logic is overly
    aggressive (e.g., collapses everything).
    """
    count, names = _count_promotable_records(catalog_dedup_distinct.workspace)
    assert count >= 3, (
        f"CATALOG-DEDUP phase C bit-flip FAILED: expected >=3 promotable "
        f"records from 4 bytewise-distinct prompts; got {count}. "
        f"_existing_auto_note_by_fingerprint may be collapsing too "
        f"aggressively, or _classify_text rejected most kinds. "
        f"names={names!r}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
