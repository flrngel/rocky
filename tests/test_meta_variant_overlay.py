"""Phase 3 T-META-4 — `apply_variant_edits` overlay purity.

Covers SC-OVERLAY-IDENTITY (A11, CF-4):
  * Identity overlay (empty edits) preserves baseline.
  * Real edit mutates only the targeted field; baseline dataclass unchanged.
  * Retrieval edits do not leak into packing (and vice versa).
  * Blocked / unknown-keyed edits raise SafetyViolation at overlay time.

Sensitivity: if `apply_variant_edits` accidentally mutated the baseline
in-place (ugly shared-state bug), `test_identity_does_not_mutate_baseline`
would fail when we check the retained defaults.
"""

from __future__ import annotations

import pytest

from rocky.config.models import PackingConfig, RetrievalConfig
from rocky.meta.overlay import apply_variant_edits
from rocky.meta.safety import SafetyViolation


def test_identity_overlay_returns_baseline_values() -> None:
    baseline_r = RetrievalConfig()
    baseline_p = PackingConfig()
    overlaid_r, overlaid_p = apply_variant_edits(baseline_r, baseline_p, {})
    assert overlaid_r.top_k_limit == baseline_r.top_k_limit
    assert overlaid_p.workspace_brief_budget == baseline_p.workspace_brief_budget
    assert overlaid_r.authority_weight == baseline_r.authority_weight


def test_identity_does_not_mutate_baseline() -> None:
    baseline_r = RetrievalConfig()
    baseline_p = PackingConfig()
    apply_variant_edits(baseline_r, baseline_p, {"retrieval.top_k_limit": 2})
    # Baseline instances untouched.
    assert baseline_r.top_k_limit == 8
    assert baseline_p.procedural_cap == 6


def test_retrieval_edit_targets_retrieval_only() -> None:
    overlaid_r, overlaid_p = apply_variant_edits(
        RetrievalConfig(), PackingConfig(), {"retrieval.top_k_limit": 3}
    )
    assert overlaid_r.top_k_limit == 3
    # Packing untouched.
    assert overlaid_p.procedural_cap == 6


def test_packing_edit_targets_packing_only() -> None:
    overlaid_r, overlaid_p = apply_variant_edits(
        RetrievalConfig(), PackingConfig(), {"packing.procedural_cap": 2}
    )
    assert overlaid_p.procedural_cap == 2
    # Retrieval untouched.
    assert overlaid_r.top_k_limit == 8


def test_nested_dict_edit_applies_to_weight_subtree() -> None:
    overlaid_r, _ = apply_variant_edits(
        RetrievalConfig(),
        PackingConfig(),
        {"retrieval.authority_weight.teacher": 9},
    )
    assert overlaid_r.authority_weight["teacher"] == 9
    # Other keys in the dict preserved.
    assert overlaid_r.authority_weight["evidence_backed"] == 3


def test_nested_weight_edit_does_not_mutate_baseline() -> None:
    baseline_r = RetrievalConfig()
    original_authority = dict(baseline_r.authority_weight)
    apply_variant_edits(
        baseline_r,
        PackingConfig(),
        {"retrieval.authority_weight.teacher": 7},
    )
    assert baseline_r.authority_weight == original_authority


def test_blocked_edit_raises_at_overlay_time() -> None:
    with pytest.raises(SafetyViolation):
        apply_variant_edits(
            RetrievalConfig(),
            PackingConfig(),
            {"permissions.mode": "auto"},
        )


def test_unknown_field_raises_safety_violation() -> None:
    # Keys outside ALLOWED_KEYS hit the allow-list guard before reaching
    # `_apply_to_config`, which is the desired order. We assert the
    # allow-list guard bites first.
    with pytest.raises(SafetyViolation):
        apply_variant_edits(
            RetrievalConfig(),
            PackingConfig(),
            {"retrieval.does_not_exist": 1},
        )
