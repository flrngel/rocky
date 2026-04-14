"""MetaVariant schema + per-variant append-only storage (PRD Phase 3 §14/§16.6)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rocky.util.time import utc_iso


_VALID_ARCHIVE_ROLES = {"baseline", "branch", "promoted"}
_VALID_PROMOTION_STATES = {"candidate", "validated", "promoted", "rolled_back"}


@dataclass(slots=True)
class MetaVariant:
    """Canonical Phase-3 meta-variant record.

    Fields carry PRD §14 requirements exactly:
      - `variant_id`: stable id. Baseline is always `"baseline"`.
      - `parent_variant_id`: the variant this branched from (or "baseline").
      - `edits`: dotted-path → scalar overlay. Validated against
        `rocky.meta.safety.validate_edits` at every write and activation site.
      - `archive_role`: one of `baseline`, `branch`, `promoted`.
      - `canary_results`: append-only list of the last-N canary runs for this
        variant (each entry is a metrics dict).
      - `created_at` / `promoted_at` / `rolled_back_at`: ISO-8601 timestamps.
      - `promotion_state`: mirrors the learning-ledger taxonomy so that
        downstream UX can treat meta-records with the same vocabulary.
    """

    variant_id: str
    parent_variant_id: str
    edits: dict[str, Any]
    archive_role: str
    canary_results: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    promoted_at: str | None = None
    rolled_back_at: str | None = None
    promotion_state: str = "candidate"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MetaVariant":
        data = dict(payload)
        data.setdefault("canary_results", [])
        data.setdefault("promoted_at", None)
        data.setdefault("rolled_back_at", None)
        data.setdefault("promotion_state", "candidate")
        data.setdefault("notes", "")
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def validate_invariants(self) -> None:
        if self.archive_role not in _VALID_ARCHIVE_ROLES:
            raise ValueError(
                f"archive_role must be one of {_VALID_ARCHIVE_ROLES}, got {self.archive_role!r}"
            )
        if self.promotion_state not in _VALID_PROMOTION_STATES:
            raise ValueError(
                f"promotion_state must be one of {_VALID_PROMOTION_STATES}, got {self.promotion_state!r}"
            )
        if not self.variant_id or "/" in self.variant_id or ".." in self.variant_id:
            raise ValueError(
                f"variant_id must be non-empty and filesystem-safe, got {self.variant_id!r}"
            )


class MetaVariantStore:
    """Per-variant append-only directory layout under `.rocky/meta/variants/`.

    Layout:
        <workspace>/.rocky/meta/variants/<variant_id>/variant.json   — canonical record
        <workspace>/.rocky/meta/variants/<variant_id>/canary.jsonl   — canary run log (append-only)

    `variant.json` is written once on creation and then only rewritten for
    state transitions (promotion / rollback) via the registry. Canary
    results are appended to `canary.jsonl` AND mirrored into the record's
    `canary_results` list so readers can fetch either.
    """

    def __init__(self, workspace_root: Path, *, create_layout: bool = True) -> None:
        self.workspace_root = Path(workspace_root)
        self.meta_root = self.workspace_root / ".rocky" / "meta"
        self.variants_root = self.meta_root / "variants"
        if create_layout:
            self.variants_root.mkdir(parents=True, exist_ok=True)

    def variant_dir(self, variant_id: str) -> Path:
        return self.variants_root / variant_id

    def variant_path(self, variant_id: str) -> Path:
        return self.variant_dir(variant_id) / "variant.json"

    def canary_log_path(self, variant_id: str) -> Path:
        return self.variant_dir(variant_id) / "canary.jsonl"

    def exists(self, variant_id: str) -> bool:
        return self.variant_path(variant_id).exists()

    def write_new(self, variant: MetaVariant) -> Path:
        """Write a new variant. Refuses to overwrite an existing variant.json."""
        variant.validate_invariants()
        target = self.variant_path(variant.variant_id)
        if target.exists():
            raise FileExistsError(
                f"variant {variant.variant_id!r} already exists at {target}; "
                "MetaVariantStore is append-only (use rewrite_state for legal transitions)"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(variant.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    def load(self, variant_id: str) -> MetaVariant | None:
        target = self.variant_path(variant_id)
        if not target.exists():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        try:
            return MetaVariant.from_dict(payload)
        except Exception:
            return None

    def list_ids(self) -> list[str]:
        if not self.variants_root.exists():
            return []
        ids: list[str] = []
        for child in sorted(self.variants_root.iterdir()):
            if child.is_dir() and (child / "variant.json").exists():
                ids.append(child.name)
        return ids

    def list_variants(self) -> list[MetaVariant]:
        items: list[MetaVariant] = []
        for vid in self.list_ids():
            loaded = self.load(vid)
            if loaded is not None:
                items.append(loaded)
        return items

    def rewrite_state(self, variant: MetaVariant) -> None:
        """Rewrite `variant.json` to reflect a state transition.

        Only state fields (promotion_state, promoted_at, rolled_back_at,
        canary_results, archive_role) are allowed to change; the caller must
        preserve variant_id, parent_variant_id, edits, and created_at. This
        method does NOT re-validate edits (no allow-list re-check on state
        transitions) since those were already validated on create.
        """
        variant.validate_invariants()
        target = self.variant_path(variant.variant_id)
        if not target.exists():
            raise FileNotFoundError(
                f"cannot rewrite_state on missing variant {variant.variant_id!r}"
            )
        prior = self.load(variant.variant_id)
        if prior is not None:
            if prior.variant_id != variant.variant_id:
                raise ValueError("variant_id mismatch on rewrite_state")
            if prior.parent_variant_id != variant.parent_variant_id:
                raise ValueError("parent_variant_id is immutable on rewrite_state")
            if prior.edits != variant.edits:
                raise ValueError("edits are immutable on rewrite_state")
            if prior.created_at != variant.created_at:
                raise ValueError("created_at is immutable on rewrite_state")
        target.write_text(
            json.dumps(variant.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def append_canary(self, variant_id: str, metrics: dict[str, Any]) -> None:
        """Append a canary metrics payload to the variant's canary.jsonl log."""
        target = self.canary_log_path(variant_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {"recorded_at": utc_iso(), **metrics}
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def new_variant_id(prefix: str = "v") -> str:
    """Generate a short, filesystem-safe variant id."""
    import secrets

    return f"{prefix}-{secrets.token_hex(4)}"
