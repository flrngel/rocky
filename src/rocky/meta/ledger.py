"""Append-only meta-ledger (PRD §14 "archive role").

Separate from the learning ledger (`src/rocky/learning/ledger.py`) so
Phase-3 promotion/rollback events can be audited without scanning the
main learning log. Layout:

    <workspace>/.rocky/meta/meta_ledger.jsonl
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rocky.util.time import utc_iso


_VALID_EVENT_TYPES = {
    "created",
    "canary_run",
    "validated",
    "promoted",
    "activated",
    "rolled_back",
    "deactivated",
    "rejected",
}


@dataclass(slots=True)
class MetaLedgerEvent:
    """Single entry in the meta-ledger."""

    event_id: str
    event_type: str
    variant_id: str
    parent_variant_id: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MetaLedgerEvent":
        data = dict(payload)
        data.setdefault("payload", {})
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class MetaLedger:
    """Append-only JSONL log of meta-variant events."""

    def __init__(self, workspace_root: Path, *, create_layout: bool = True) -> None:
        self.workspace_root = Path(workspace_root)
        self.meta_dir = self.workspace_root / ".rocky" / "meta"
        self.log_path = self.meta_dir / "meta_ledger.jsonl"
        if create_layout:
            self.meta_dir.mkdir(parents=True, exist_ok=True)
            if not self.log_path.exists():
                self.log_path.write_text("", encoding="utf-8")

    def append(
        self,
        event_type: str,
        variant_id: str,
        parent_variant_id: str = "baseline",
        payload: dict[str, Any] | None = None,
    ) -> MetaLedgerEvent:
        if event_type not in _VALID_EVENT_TYPES:
            raise ValueError(
                f"event_type {event_type!r} not in {sorted(_VALID_EVENT_TYPES)}"
            )
        event = MetaLedgerEvent(
            event_id=_new_event_id(),
            event_type=event_type,
            variant_id=variant_id,
            parent_variant_id=parent_variant_id,
            created_at=utc_iso(),
            payload=dict(payload or {}),
        )
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
        return event

    def load_all(self) -> list[MetaLedgerEvent]:
        if not self.log_path.exists():
            return []
        events: list[MetaLedgerEvent] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            try:
                events.append(MetaLedgerEvent.from_dict(payload))
            except Exception:
                continue
        return events

    def events_for_variant(self, variant_id: str) -> list[MetaLedgerEvent]:
        return [e for e in self.load_all() if e.variant_id == variant_id]


def _new_event_id() -> str:
    import secrets

    return f"mev-{secrets.token_hex(5)}"
