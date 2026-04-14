"""Phase 3 T-META-6 — MetaLedger append-only JSONL round-trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rocky.meta.ledger import MetaLedger, MetaLedgerEvent


def test_append_and_load(tmp_path: Path) -> None:
    ledger = MetaLedger(tmp_path)
    event = ledger.append("created", variant_id="v-1", payload={"edits": {}})
    assert event.event_type == "created"
    loaded = ledger.load_all()
    assert len(loaded) == 1
    assert loaded[0].variant_id == "v-1"


def test_events_for_variant_filters(tmp_path: Path) -> None:
    ledger = MetaLedger(tmp_path)
    ledger.append("created", variant_id="v-1")
    ledger.append("created", variant_id="v-2")
    ledger.append("canary_run", variant_id="v-1", payload={"metric": 1})
    events = ledger.events_for_variant("v-1")
    assert [e.event_type for e in events] == ["created", "canary_run"]


def test_invalid_event_type_rejected(tmp_path: Path) -> None:
    ledger = MetaLedger(tmp_path)
    with pytest.raises(ValueError):
        ledger.append("garbage_type", variant_id="v-1")


def test_log_is_jsonl_append_only(tmp_path: Path) -> None:
    ledger = MetaLedger(tmp_path)
    for i in range(3):
        ledger.append("created", variant_id=f"v-{i}")
    lines = ledger.log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        parsed = json.loads(line)
        assert parsed["event_type"] == "created"


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    ledger = MetaLedger(tmp_path)
    ledger.append("created", variant_id="v-ok")
    with ledger.log_path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    ledger.append("validated", variant_id="v-ok")
    events = ledger.load_all()
    # Corrupt line dropped; the two legitimate events remain.
    assert [e.event_type for e in events] == ["created", "validated"]


def test_from_dict_round_trip() -> None:
    payload = {
        "event_id": "mev-abcdef",
        "event_type": "created",
        "variant_id": "v-1",
        "parent_variant_id": "baseline",
        "created_at": "2026-04-14T00:00:00Z",
        "payload": {"edits": {"retrieval.top_k_limit": 2}},
    }
    event = MetaLedgerEvent.from_dict(payload)
    assert event.to_dict() == payload
