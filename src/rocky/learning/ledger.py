"""Canonical Learning Ledger (PRD Phase 1, run-20260412-142114).

The ledger is the single source of truth for durable self-learning artifacts.
Each `/teach` event or autonomous capture produces exactly one canonical
`LearningRecord` carrying a `lineage_id`. The ledger also indexes the
filesystem paths of legacy artifacts (policy dirs, student notes, memory
candidates, etc.) registered under that lineage so `rollback_lineage`
can move all of them atomically — closing the PRD §8 Issue 1 multi-store
leak.

Pragmatic scope for Phase 1:
    - Dataclass with the 17 PRD §8.2 fields + version/rollback bookkeeping.
    - JSONL canonical log + JSON lineage index for atomic rollback.
    - Legacy stores keep writing their own artifacts; the ledger registers
      those paths by lineage_id. This lets `/undo` move them all without
      requiring a full retriever rewrite (that is Phase 2 work).

Honesty note:
    - A4 "retrievers read from ledger first" is PARTIALLY covered here at
      the write-registration layer (paths indexed, records logged). Full
      retriever-reads-ledger is Phase 2 scope.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rocky.util.time import utc_iso


@dataclass(slots=True)
class LearningRecord:
    """Canonical durable-learning record per PRD §8.2.

    The 17 named fields are required by the PRD schema. `ledger_version`,
    `rolled_back`, and `rolled_back_at` are bookkeeping fields added for
    migration idempotency and rollback state tracking.
    """

    id: str
    kind: str
    scope: str
    authority: str
    promotion_state: str
    activation_mode: str
    task_signature: str
    task_family: str
    failure_class: str | None
    triggers: list[str]
    required_behavior: list[str]
    prohibited_behavior: list[str]
    evidence: list[str]
    lineage: dict[str, Any]
    created_at: str
    updated_at: str
    origin: dict[str, Any]
    reuse_stats: dict[str, Any]
    ledger_version: int = 1
    rolled_back: bool = False
    rolled_back_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LearningRecord":
        data = dict(payload)
        data.setdefault("ledger_version", 1)
        data.setdefault("rolled_back", False)
        data.setdefault("rolled_back_at", None)
        data.setdefault("failure_class", None)
        for list_field in ("triggers", "required_behavior", "prohibited_behavior", "evidence"):
            data.setdefault(list_field, [])
        for dict_field in ("lineage", "origin", "reuse_stats"):
            data.setdefault(dict_field, {})
        known_fields = {f for f in LearningRecord.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class LearningLedgerStore:
    """Append-only canonical record log + lineage→paths index.

    Layout:
        <workspace>/.rocky/ledger/records.jsonl   — one LearningRecord JSON per line.
        <workspace>/.rocky/ledger/lineage_index.json — {lineage_id: [path, ...]}.

    Operations are best-effort atomic within the constraints of the
    filesystem — the records log is append-only; the index is rewritten
    on every change. Rollback marks records as rolled_back (never deletes)
    and moves registered paths out of the workspace proper.
    """

    def __init__(self, workspace_root: Path, *, create_layout: bool = True) -> None:
        self.workspace_root = Path(workspace_root)
        self.ledger_dir = self.workspace_root / ".rocky" / "ledger"
        self.records_path = self.ledger_dir / "records.jsonl"
        self.index_path = self.ledger_dir / "lineage_index.json"
        if create_layout:
            self.ledger_dir.mkdir(parents=True, exist_ok=True)
            if not self.records_path.exists():
                self.records_path.write_text("", encoding="utf-8")
            if not self.index_path.exists():
                self.index_path.write_text("{}", encoding="utf-8")

    # ---- record log ----

    def append(self, record: LearningRecord) -> None:
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        with self.records_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def load_all(self) -> list[LearningRecord]:
        if not self.records_path.exists():
            return []
        records: list[LearningRecord] = []
        for line in self.records_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            try:
                records.append(LearningRecord.from_dict(payload))
            except Exception:
                continue
        return records

    def filter_by_kind(self, kind: str) -> list[LearningRecord]:
        return [r for r in self.load_all() if r.kind == kind]

    def lookup_by_id(self, record_id: str) -> LearningRecord | None:
        for r in self.load_all():
            if r.id == record_id:
                return r
        return None

    def lookup_by_lineage(self, lineage_id: str) -> LearningRecord | None:
        """Return the (most recent) record whose lineage.id matches."""
        found: LearningRecord | None = None
        for r in self.load_all():
            if (r.lineage or {}).get("id") == lineage_id:
                found = r  # keep last occurrence (most recent)
        return found

    def find_teach_lineage_for_policy(self, policy_id: str) -> str | None:
        """Return the teach lineage_id that produced the given policy, if any.

        Scans teacher_feedback-origin records for a matching `lineage.policy_id`.
        Returns the lineage id (not the record id — they're equal for teach records,
        but this is the lineage-key external callers should use). Skips rolled-back
        records so a stale lineage doesn't leak back through a fresh capture.
        """
        if not policy_id:
            return None
        for record in self.load_all():
            if record.rolled_back:
                continue
            origin_type = str((record.origin or {}).get("type") or "").lower()
            if origin_type not in {"teacher_feedback", "user_feedback"}:
                continue
            lineage = record.lineage or {}
            if str(lineage.get("policy_id") or "") == policy_id:
                return str(lineage.get("id") or record.id)
        return None

    def latest_teach_lineage(self) -> LearningRecord | None:
        """Return the most recently appended record whose origin represents a teach event.

        Teach-originated records are those with `origin.type` in
        `{"teacher_feedback", "user_feedback"}` and not yet rolled back.
        """
        for record in reversed(self.load_all()):
            if record.rolled_back:
                continue
            origin_type = str((record.origin or {}).get("type") or "").lower()
            if origin_type in {"teacher_feedback", "user_feedback"}:
                return record
        return None

    # ---- lineage index ----

    def _read_index(self) -> dict[str, list[str]]:
        if not self.index_path.exists():
            return {}
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): [str(p) for p in (v or [])] for k, v in raw.items()}

    def _write_index(self, index: dict[str, list[str]]) -> None:
        self.index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def register_artifact(self, lineage_id: str, path: Path | str) -> None:
        if not lineage_id:
            return
        index = self._read_index()
        paths = list(index.get(lineage_id, []))
        path_str = str(path)
        if path_str not in paths:
            paths.append(path_str)
        index[lineage_id] = paths
        self._write_index(index)

    def artifacts_for_lineage(self, lineage_id: str) -> list[Path]:
        index = self._read_index()
        return [Path(p) for p in index.get(lineage_id, [])]

    # ---- rollback + state ----

    def mark_rolled_back(self, record_id: str) -> None:
        """Rewrite the records log with `rolled_back=True` on the matching id.

        Append-only semantics are bent here: we rewrite the log in place to
        avoid a separate mutation log. Safe because the log is workspace-local
        and small; the ledger_version stamp is preserved.
        """
        records = self.load_all()
        stamp = utc_iso()
        changed = False
        for record in records:
            if record.id == record_id and not record.rolled_back:
                record.rolled_back = True
                record.rolled_back_at = stamp
                changed = True
        if not changed:
            return
        self.records_path.write_text(
            "\n".join(json.dumps(r.to_dict(), ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )

    def is_path_in_rolled_back_lineage(self, path: Path | str) -> bool:
        """Return True iff `path` is registered under any rolled-back lineage.

        Belt-and-suspenders guard for T4 — catches the case where an artifact's
        file still exists on disk but the lineage that produced it has been
        rolled back. The primary path (lineage-based rollback moving the file)
        should already make this moot, but retrievers consulting this helper
        remain safe against partial/stale states.
        """
        target = str(path)
        if not target:
            return False
        index = self._read_index()
        for lineage_id, paths in index.items():
            if target not in paths:
                continue
            if self.is_lineage_rolled_back(lineage_id):
                return True
        # Also check lineages not in index but whose record is rolled back and
        # would have referenced this path (records hold `lineage.path` too).
        for record in self.load_all():
            if not record.rolled_back:
                continue
            lineage = record.lineage or {}
            recorded_path = str(lineage.get("path") or "")
            if recorded_path and recorded_path == target:
                return True
        return False

    def is_lineage_rolled_back(self, lineage_id: str) -> bool:
        """Return True iff any record with `lineage.id == lineage_id` is marked rolled_back."""
        if not lineage_id:
            return False
        for record in self.load_all():
            if (record.lineage or {}).get("id") != lineage_id:
                continue
            if record.rolled_back:
                return True
        return False

    def rollback_lineage(self, lineage_id: str, rollback_root: Path) -> dict[str, Any]:
        """Move all indexed artifacts for the lineage into rollback_root.

        Returns a dict with `rolled_back: bool`, `lineage_id`, and `moved:
        list[{src, dst}]`. Unrelated records/lineages are untouched. Marks
        the matching record in the canonical log as rolled_back.
        """
        lineage_paths = self.artifacts_for_lineage(lineage_id)
        if not lineage_paths:
            record = self.lookup_by_lineage(lineage_id)
            if record is not None:
                self.mark_rolled_back(record.id)
            return {"rolled_back": record is not None, "lineage_id": lineage_id, "moved": []}

        rollback_dir = rollback_root / f"{lineage_id}__{utc_iso().replace(':', '').replace('-', '')}"
        rollback_dir.mkdir(parents=True, exist_ok=True)
        moved: list[dict[str, str]] = []
        for src_str in lineage_paths:
            src = Path(src_str)
            if not src.exists():
                continue
            try:
                rel = src.relative_to(self.workspace_root)
                dst = rollback_dir / rel
            except ValueError:
                dst = rollback_dir / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                moved.append({"src": str(src), "dst": str(dst)})
            except Exception:
                continue

        # Remove the rolled-back paths from the index.
        index = self._read_index()
        if lineage_id in index:
            remaining = [
                p for p in index[lineage_id] if not any(m["src"] == p for m in moved)
            ]
            if remaining:
                index[lineage_id] = remaining
            else:
                index.pop(lineage_id, None)
            self._write_index(index)

        record = self.lookup_by_lineage(lineage_id)
        if record is not None:
            self.mark_rolled_back(record.id)

        return {
            "rolled_back": bool(moved) or record is not None,
            "lineage_id": lineage_id,
            "record_id": record.id if record else None,
            "moved": moved,
        }


def new_lineage_id(prefix: str = "ln") -> str:
    """Generate a fresh lineage_id. Short enough for filesystem paths, unique enough per run."""
    import secrets

    return f"{prefix}-{secrets.token_hex(6)}"


def migrate_legacy_workspace(
    ledger: LearningLedgerStore, workspace_root: Path
) -> dict[str, int]:
    """Idempotent migration from legacy stores into the ledger.

    Runs on RockyRuntime.load_from bootstrap. Skips if the ledger already
    contains records (identified by ledger_version stamp or by existing
    lineage.id).

    Walks:
      - .rocky/policies/learned/*/POLICY.meta.json → kind=procedure
      - .rocky/student/notebook.jsonl entries      → kind=lesson
      - .rocky/student/patterns/*.md               → kind=procedure (migration)
      - .rocky/student/examples/*.md               → kind=example
      - .rocky/student/retrospectives/*.md         → kind=retrospective
      - .rocky/memories/candidates/*.json          → kind per stored `kind`
      - .rocky/memories/auto/*.json                → kind per stored `kind`
      - .rocky/memories/project_brief.md           → kind=workspace_brief

    Each migrated record is stamped ledger_version=1. Re-running migration
    skips any lineage.id already present in the ledger.
    """
    workspace_root = Path(workspace_root)
    existing_ids = {
        str((r.lineage or {}).get("id") or "") for r in ledger.load_all()
    }
    existing_ids.discard("")
    rocky_root = workspace_root / ".rocky"
    counters = {"migrated": 0, "already_present": 0}

    def _append_if_new(lineage_id: str, record: LearningRecord, register_path: Path | None) -> None:
        if lineage_id in existing_ids:
            counters["already_present"] += 1
            return
        ledger.append(record)
        if register_path is not None and register_path.exists():
            ledger.register_artifact(lineage_id, register_path)
        existing_ids.add(lineage_id)
        counters["migrated"] += 1

    stamp = utc_iso()

    # 1. Learned policies
    policies_root = rocky_root / "policies" / "learned"
    if policies_root.exists():
        for meta_path in sorted(policies_root.rglob("POLICY.meta.json")):
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            policy_id = str(payload.get("policy_id") or meta_path.parent.name)
            lineage_id = f"mig-pol-{policy_id}"
            record = LearningRecord(
                id=lineage_id,
                kind="procedure",
                scope=str(payload.get("scope") or "project"),
                authority="teacher",
                promotion_state=str(
                    payload.get("promotion_state") or (payload.get("metadata") or {}).get("promotion_state") or "candidate"
                ),
                activation_mode="soft",
                task_signature=str(
                    (payload.get("metadata") or {}).get("task_signatures", [""])[0]
                    if (payload.get("metadata") or {}).get("task_signatures")
                    else ""
                ),
                task_family=str(payload.get("task_family") or ""),
                failure_class=payload.get("failure_class"),
                triggers=list(((payload.get("metadata") or {}).get("retrieval") or {}).get("triggers") or []),
                required_behavior=list((payload.get("metadata") or {}).get("required_behavior") or []),
                prohibited_behavior=list((payload.get("metadata") or {}).get("prohibited_behavior") or []),
                evidence=[],
                lineage={"id": lineage_id, "policy_id": policy_id, "source": "migration_policy"},
                created_at=str(payload.get("published_at") or stamp),
                updated_at=stamp,
                origin={"type": "migration_policy", "path": str(meta_path)},
                reuse_stats={
                    "reuse_count": int((payload.get("metadata") or {}).get("reuse_count") or 0),
                    "verified_success_count": int(
                        (payload.get("metadata") or {}).get("verified_success_count") or 0
                    ),
                },
            )
            _append_if_new(lineage_id, record, meta_path.parent)

    # 2. Student notebook
    notebook_path = rocky_root / "student" / "notebook.jsonl"
    if notebook_path.exists():
        for line in notebook_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except Exception:
                continue
            entry_id = str(entry.get("id") or "")
            if not entry_id:
                continue
            lineage_id = f"mig-lesson-{entry_id}"
            record = LearningRecord(
                id=lineage_id,
                kind=str(entry.get("kind") or "lesson"),
                scope="project",
                authority="teacher",
                promotion_state="promoted",
                activation_mode="soft",
                task_signature=str(entry.get("task_signature") or ""),
                task_family="",
                failure_class=entry.get("failure_class"),
                triggers=list(entry.get("tags") or []),
                required_behavior=[],
                prohibited_behavior=[],
                evidence=[],
                lineage={"id": lineage_id, "entry_id": entry_id, "source": "migration_notebook"},
                created_at=str(entry.get("created_at") or stamp),
                updated_at=stamp,
                origin={"type": "migration_notebook", "path": str(notebook_path)},
                reuse_stats={},
            )
            _append_if_new(lineage_id, record, None)

    # 3. Student markdown stores
    for sub, kind in (("patterns", "procedure"), ("examples", "example"), ("retrospectives", "retrospective")):
        md_root = rocky_root / "student" / sub
        if not md_root.exists():
            continue
        for md_path in sorted(md_root.glob("*.md")):
            lineage_id = f"mig-{sub[:3]}-{md_path.stem}"
            record = LearningRecord(
                id=lineage_id,
                kind=kind,
                scope="project",
                authority="self_generated" if kind == "retrospective" else "teacher",
                promotion_state="promoted",
                activation_mode="soft",
                task_signature="",
                task_family="",
                failure_class=None,
                triggers=[],
                required_behavior=[],
                prohibited_behavior=[],
                evidence=[],
                lineage={"id": lineage_id, "source": f"migration_student_{sub}", "path": str(md_path)},
                created_at=stamp,
                updated_at=stamp,
                origin={"type": f"migration_student_{sub}", "path": str(md_path)},
                reuse_stats={},
            )
            _append_if_new(lineage_id, record, md_path)

    # 4. Memory candidates + auto
    for sub in ("candidates", "auto"):
        mem_root = rocky_root / "memories" / sub
        if not mem_root.exists():
            continue
        for json_path in sorted(mem_root.glob("*.json")):
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            mem_id = str(payload.get("id") or json_path.stem)
            lineage_id = f"mig-mem-{mem_id}"
            record = LearningRecord(
                id=lineage_id,
                kind=str(payload.get("kind") or "preference"),
                scope=str(payload.get("scope") or "project_auto"),
                authority="evidence_backed",
                promotion_state=str(payload.get("promotion_state") or ("promoted" if sub == "auto" else "candidate")),
                activation_mode="soft",
                task_signature=str(payload.get("source_task_signature") or ""),
                task_family="",
                failure_class=None,
                triggers=[],
                required_behavior=[],
                prohibited_behavior=[],
                evidence=[],
                lineage={"id": lineage_id, "memory_id": mem_id, "source": f"migration_memory_{sub}"},
                created_at=str(payload.get("created_at") or stamp),
                updated_at=stamp,
                origin={"type": f"migration_memory_{sub}", "path": str(json_path)},
                reuse_stats={},
            )
            _append_if_new(lineage_id, record, json_path)

    # 5. Project brief
    brief_path = rocky_root / "memories" / "project_brief.md"
    if brief_path.exists():
        lineage_id = "mig-brief"
        record = LearningRecord(
            id=lineage_id,
            kind="workspace_brief",
            scope="project",
            authority="evidence_backed",
            promotion_state="promoted",
            activation_mode="soft",
            task_signature="",
            task_family="",
            failure_class=None,
            triggers=[],
            required_behavior=[],
            prohibited_behavior=[],
            evidence=[],
            lineage={"id": lineage_id, "composite": True, "source": "migration_project_brief"},
            created_at=stamp,
            updated_at=stamp,
            origin={"type": "migration_project_brief", "path": str(brief_path)},
            reuse_stats={},
        )
        _append_if_new(lineage_id, record, brief_path)

    return counters
