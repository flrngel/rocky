"""Phase 3 T-META-1 — MetaVariant dataclass + MetaVariantStore round-trip.

Covers SC-SCHEMA (A1, A2 partial):
  * dataclass round-trip (to_dict / from_dict identity)
  * append-only refusal on second write
  * stable filesystem layout under `.rocky/meta/variants/<id>/`
  * load() returns None on corrupt or missing files (sensitivity witness
    for the "reload doesn't raise at unrelated call sites" invariant)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rocky.meta.variants import MetaVariant, MetaVariantStore, new_variant_id


def _make_variant(**overrides) -> MetaVariant:
    base = dict(
        variant_id="v-abc",
        parent_variant_id="baseline",
        edits={"retrieval.top_k_limit": 2},
        archive_role="branch",
        canary_results=[],
        created_at="2026-04-14T00:00:00Z",
        promoted_at=None,
        rolled_back_at=None,
        promotion_state="candidate",
        notes="",
    )
    base.update(overrides)
    return MetaVariant(**base)


def test_schema_round_trip(tmp_path: Path) -> None:
    variant = _make_variant()
    payload = variant.to_dict()
    restored = MetaVariant.from_dict(payload)
    assert restored == variant
    # Every PRD §14 field is present.
    for field in (
        "variant_id",
        "parent_variant_id",
        "edits",
        "archive_role",
        "canary_results",
        "created_at",
        "promoted_at",
        "rolled_back_at",
    ):
        assert field in payload


def test_store_write_and_load(tmp_path: Path) -> None:
    store = MetaVariantStore(tmp_path)
    variant = _make_variant(variant_id="v-round1")
    path = store.write_new(variant)
    assert path.exists()
    assert path == tmp_path / ".rocky" / "meta" / "variants" / "v-round1" / "variant.json"
    loaded = store.load("v-round1")
    assert loaded is not None
    assert loaded.edits == variant.edits
    assert store.list_ids() == ["v-round1"]


def test_store_append_only_refuses_overwrite(tmp_path: Path) -> None:
    store = MetaVariantStore(tmp_path)
    store.write_new(_make_variant(variant_id="v-once"))
    with pytest.raises(FileExistsError):
        store.write_new(_make_variant(variant_id="v-once", notes="second write"))


def test_store_load_returns_none_on_missing(tmp_path: Path) -> None:
    store = MetaVariantStore(tmp_path)
    assert store.load("never-created") is None


def test_store_load_returns_none_on_corrupt(tmp_path: Path) -> None:
    store = MetaVariantStore(tmp_path)
    store.write_new(_make_variant(variant_id="v-corrupt"))
    target = store.variant_path("v-corrupt")
    target.write_text("{not json", encoding="utf-8")
    # Must not raise — this is the "unrelated call sites still work" invariant.
    assert store.load("v-corrupt") is None
    # Neighbours still load cleanly.
    store.write_new(_make_variant(variant_id="v-ok"))
    assert store.load("v-ok") is not None


def test_store_rewrite_state_preserves_immutable_fields(tmp_path: Path) -> None:
    store = MetaVariantStore(tmp_path)
    original = _make_variant(variant_id="v-state")
    store.write_new(original)
    updated = _make_variant(
        variant_id="v-state",
        promotion_state="validated",
        canary_results=[{"ok": True}],
    )
    store.rewrite_state(updated)
    loaded = store.load("v-state")
    assert loaded.promotion_state == "validated"
    assert loaded.canary_results == [{"ok": True}]

    # Attempting to mutate an immutable field must raise.
    with pytest.raises(ValueError):
        store.rewrite_state(
            _make_variant(variant_id="v-state", edits={"retrieval.top_k_limit": 99})
        )


def test_store_append_canary(tmp_path: Path) -> None:
    store = MetaVariantStore(tmp_path)
    store.write_new(_make_variant(variant_id="v-canary"))
    store.append_canary("v-canary", {"top1": "a", "chars": 123})
    store.append_canary("v-canary", {"top1": "b", "chars": 124})
    log_path = store.canary_log_path("v-canary")
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    decoded = [json.loads(line) for line in lines]
    assert decoded[0]["chars"] == 123
    assert decoded[1]["chars"] == 124
    assert "recorded_at" in decoded[0]


def test_new_variant_id_unique_and_safe() -> None:
    ids = {new_variant_id() for _ in range(64)}
    assert len(ids) == 64
    for vid in ids:
        assert "/" not in vid
        assert ".." not in vid


def test_variant_invariants_reject_unsafe_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _make_variant(variant_id="../escape").validate_invariants()
    with pytest.raises(ValueError):
        _make_variant(variant_id="bad/id").validate_invariants()
    with pytest.raises(ValueError):
        _make_variant(archive_role="bogus").validate_invariants()
    with pytest.raises(ValueError):
        _make_variant(promotion_state="unreal").validate_invariants()
