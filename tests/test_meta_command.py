"""Phase 3 T-META-9 — `cmd_meta` subcommand end-to-end.

Covers SC-CLI (A12):
  * `/meta list` before any create returns an empty variants list.
  * `/meta create` accepts a JSON edits payload and creates a candidate.
  * `/meta create` with a blocked edit returns a named `ok: False` result.
  * `/meta canary` runs offline and returns aggregate metrics.
  * `/meta activate` on a validated variant flips the active pointer.
  * `/meta rollback` resets to baseline and the overlay goes back to defaults.
  * Invalid JSON edits return an `ok: False` error (not a crash).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rocky.app import RockyRuntime


def _runtime(tmp_path: Path) -> RockyRuntime:
    return RockyRuntime.load_from(tmp_path)


def test_meta_list_on_empty_workspace(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    result = runtime.commands.handle("/meta list")
    assert result.name == "meta"
    assert result.data["active_id"] == "baseline"
    assert result.data["variants"] == []


def test_meta_create_and_show(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    r = runtime.commands.handle(
        """/meta create v-a baseline '{"retrieval.top_k_limit": 2}'"""
    )
    assert r.data["promotion_state"] == "candidate"
    assert r.data["edits"] == {"retrieval.top_k_limit": 2}
    r2 = runtime.commands.handle("/meta show v-a")
    assert r2.data["variant_id"] == "v-a"


def test_meta_create_rejects_blocked_edit(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    r = runtime.commands.handle(
        """/meta create v-bad baseline '{"permissions.mode": "auto"}'"""
    )
    assert r.data.get("ok") is False
    assert r.data.get("violation_key") == "permissions.mode"


def test_meta_create_rejects_invalid_json(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    r = runtime.commands.handle("/meta create v-bad baseline not-json-here")
    assert r.data.get("ok") is False
    assert "valid JSON" in r.data["reason"]


def test_meta_canary_activate_rollback_end_to_end(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.commands.handle(
        """/meta create v-topk baseline '{"retrieval.top_k_limit": 2}'"""
    )
    r = runtime.commands.handle("/meta canary v-topk")
    assert r.data["aggregate"]["differs_from_baseline"] is True
    r2 = runtime.commands.handle("/meta activate v-topk")
    assert r2.data["promotion_state"] == "promoted"

    # The active overlay reflects the variant.
    r3 = runtime.commands.handle("/meta active")
    assert r3.data["active_id"] == "v-topk"
    assert r3.data["retrieval_top_k_limit"] == 2

    r4 = runtime.commands.handle("/meta rollback v-topk")
    assert r4.data["promotion_state"] == "rolled_back"
    r5 = runtime.commands.handle("/meta active")
    assert r5.data["active_id"] == "baseline"
    assert r5.data["retrieval_top_k_limit"] == 8


def test_meta_activate_on_candidate_errors(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.commands.handle(
        """/meta create v-raw baseline '{"retrieval.top_k_limit": 2}'"""
    )
    r = runtime.commands.handle("/meta activate v-raw")
    # No canary run yet → activate errors with VariantStateError mapped to ok=False.
    assert r.data.get("ok") is False
    assert "validated" in r.data.get("reason", "").lower()


def test_unknown_subcommand_surfaces_usage(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    r = runtime.commands.handle("/meta bogus")
    assert r.data.get("ok") is False
    assert "Usage" in r.data["reason"]
