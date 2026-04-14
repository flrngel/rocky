"""Phase 3 T-META-6/7 — promotion state machine + rollback pointer flip.

Covers SC-PROMOTION (A4, A5):
  * candidate → validated → promoted transitions are gated as spec.md says.
  * activating a `candidate` variant (no canary) raises `VariantStateError`.
  * `active.json` pointer flips on activate and on rollback.
  * `rollback` stamps `rolled_back_at` and returns the world to baseline.
  * `apply_active_overlay` returns the overlaid retrieval config when a variant
    is active, and baseline when none is active.
  * Meta-ledger records `created`, `canary_run`, `validated`, `activated`,
    `promoted`, and `rolled_back` events with monotonically-increasing
    timestamps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rocky.meta.registry import BASELINE_ID, MetaVariantRegistry, VariantStateError
from rocky.meta.safety import SafetyViolation


def test_clean_registry_is_baseline(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    assert reg.active_id() == BASELINE_ID
    assert reg.is_baseline_active()
    overlay = reg.apply_active_overlay()
    assert overlay.active_id == BASELINE_ID
    assert overlay.retrieval.top_k_limit == 8  # baseline default


def test_create_variant_lands_as_candidate(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    variant = reg.create_variant("v-a", {"retrieval.top_k_limit": 2})
    assert variant.promotion_state == "candidate"
    assert variant.edits == {"retrieval.top_k_limit": 2}
    # Meta-ledger event was logged.
    events = reg.ledger.events_for_variant("v-a")
    assert [e.event_type for e in events] == ["created"]


def test_create_variant_refuses_duplicate_id(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-dup", {"retrieval.top_k_limit": 2})
    with pytest.raises(FileExistsError):
        reg.create_variant("v-dup", {"retrieval.top_k_limit": 3})


def test_create_variant_rejects_baseline_id(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    with pytest.raises(ValueError):
        reg.create_variant(BASELINE_ID, {"retrieval.top_k_limit": 2})


def test_create_variant_rejects_unsafe_edit(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    with pytest.raises(SafetyViolation):
        reg.create_variant("v-bad", {"permissions.mode": "auto"})
    # No variant dir was created.
    assert not (tmp_path / ".rocky" / "meta" / "variants" / "v-bad").exists()


def test_canary_flips_state_to_validated(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-topk", {"retrieval.top_k_limit": 2})
    result = reg.canary("v-topk")
    assert result.aggregate["differs_from_baseline"] is True
    variant = reg.show("v-topk")
    assert variant is not None
    assert variant.promotion_state == "validated"
    event_types = [e.event_type for e in reg.ledger.events_for_variant("v-topk")]
    assert "canary_run" in event_types
    assert "validated" in event_types


def test_activate_gated_on_validated_state(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-untested", {"retrieval.top_k_limit": 2})
    with pytest.raises(VariantStateError):
        reg.activate("v-untested")


def test_activate_flips_pointer_and_promotes(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-topk", {"retrieval.top_k_limit": 2})
    reg.canary("v-topk")
    promoted = reg.activate("v-topk")
    assert promoted.promotion_state == "promoted"
    assert promoted.promoted_at
    assert reg.active_id() == "v-topk"
    overlay = reg.apply_active_overlay()
    assert overlay.active_id == "v-topk"
    assert overlay.retrieval.top_k_limit == 2
    event_types = [e.event_type for e in reg.ledger.events_for_variant("v-topk")]
    assert "promoted" in event_types
    assert "activated" in event_types


def test_rollback_returns_world_to_baseline(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-topk", {"retrieval.top_k_limit": 2})
    reg.canary("v-topk")
    reg.activate("v-topk")
    rolled = reg.rollback("v-topk")
    assert rolled.promotion_state == "rolled_back"
    assert rolled.rolled_back_at
    assert reg.active_id() == BASELINE_ID
    overlay = reg.apply_active_overlay()
    assert overlay.active_id == BASELINE_ID
    assert overlay.retrieval.top_k_limit == 8


def test_rollback_preserves_prior_active_history(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-a", {"retrieval.top_k_limit": 2})
    reg.canary("v-a")
    reg.activate("v-a")
    reg.create_variant("v-b", {"retrieval.top_k_limit": 3})
    reg.canary("v-b")
    reg.activate("v-b")  # v-a goes into history
    assert reg.active_id() == "v-b"
    # Rollback the current active — should restore v-a (most recent prior).
    reg.rollback("v-b")
    assert reg.active_id() == "v-a"
    overlay = reg.apply_active_overlay()
    assert overlay.retrieval.top_k_limit == 2


def test_apply_active_overlay_recovers_from_stale_pointer(tmp_path: Path) -> None:
    """If active.json points to a missing variant, overlay falls back to baseline."""
    reg = MetaVariantRegistry(tmp_path)
    reg._write_active({"active_id": "v-ghost", "history": []})
    overlay = reg.apply_active_overlay()
    assert overlay.active_id == BASELINE_ID
    # The pointer was repaired in place.
    assert reg.active_id() == BASELINE_ID


def test_rolled_back_variant_cannot_re_activate(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-topk", {"retrieval.top_k_limit": 2})
    reg.canary("v-topk")
    reg.activate("v-topk")
    reg.rollback("v-topk")
    with pytest.raises(VariantStateError):
        reg.activate("v-topk")


def test_meta_ledger_events_monotonic(tmp_path: Path) -> None:
    reg = MetaVariantRegistry(tmp_path)
    reg.create_variant("v-topk", {"retrieval.top_k_limit": 2})
    reg.canary("v-topk")
    reg.activate("v-topk")
    reg.rollback("v-topk")
    events = reg.ledger.events_for_variant("v-topk")
    timestamps = [e.created_at for e in events]
    assert timestamps == sorted(timestamps), "meta-ledger events must be monotonic"
