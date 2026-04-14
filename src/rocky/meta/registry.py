"""MetaVariantRegistry — glue across variants / safety / ledger / canary / overlay.

Owns the active-variant pointer at `.rocky/meta/active.json` with a small
sentinel `{"active_id": "baseline"}` meaning no overlay. All state
transitions are recorded in the meta-ledger for auditability.

State machine:

    candidate ── canary-passed ──▶ validated ── activate ──▶ promoted
         │                              │                       │
         └── rollback / rejected ◀──────┴──────── rollback ─────┘

Transitions:
  * create_variant(edits)  → state=candidate, event=created.
  * canary(variant_id)     → state=validated (if delta is non-trivial), event=canary_run (+ validated).
  * activate(variant_id)   → requires state=validated (or already promoted);
                             flips active.json; state=promoted; event=activated (+ promoted).
  * rollback(variant_id)   → state=rolled_back; if active, flips active.json
                             back to the prior active; event=rolled_back.
  * apply_active_overlay(...) → pure read: returns the overlaid retrieval + packing
                                configs for the currently active variant (or
                                baseline passthrough if none).

Authority tier: the registry never allows a variant to ACTIVATE without
passing safety + canary. Three safety check sites (see
`rocky.meta.safety`) preserve CF-14 spirit at the meta layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rocky.config.models import PackingConfig, RetrievalConfig
from rocky.meta.canary import CanaryCorpus, CanaryResult, CanaryRunner, default_corpus
from rocky.meta.ledger import MetaLedger
from rocky.meta.overlay import apply_variant_edits
from rocky.meta.safety import SafetyViolation, validate_edits
from rocky.meta.variants import MetaVariant, MetaVariantStore
from rocky.util.time import utc_iso


BASELINE_ID = "baseline"


@dataclass(slots=True)
class ActiveOverlay:
    """Result of `MetaVariantRegistry.apply_active_overlay`."""

    active_id: str
    retrieval: RetrievalConfig
    packing: PackingConfig


class VariantStateError(Exception):
    """Raised when a state transition is attempted from an illegal prior state."""


class MetaVariantRegistry:
    """Top-level coordinator for the meta-learning archive."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        create_layout: bool = True,
        corpus: CanaryCorpus | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.meta_dir = self.workspace_root / ".rocky" / "meta"
        self.active_path = self.meta_dir / "active.json"
        self.store = MetaVariantStore(self.workspace_root, create_layout=create_layout)
        self.ledger = MetaLedger(self.workspace_root, create_layout=create_layout)
        self.corpus = corpus if corpus is not None else default_corpus()
        if create_layout:
            self.meta_dir.mkdir(parents=True, exist_ok=True)
            if not self.active_path.exists():
                self._write_active({"active_id": BASELINE_ID, "history": []})

    # ------------------------------------------------------------------
    # Active-variant pointer
    # ------------------------------------------------------------------

    def _read_active(self) -> dict[str, Any]:
        if not self.active_path.exists():
            return {"active_id": BASELINE_ID, "history": []}
        try:
            raw = json.loads(self.active_path.read_text(encoding="utf-8"))
        except Exception:
            return {"active_id": BASELINE_ID, "history": []}
        if not isinstance(raw, dict):
            return {"active_id": BASELINE_ID, "history": []}
        raw.setdefault("active_id", BASELINE_ID)
        raw.setdefault("history", [])
        return raw

    def _write_active(self, data: dict[str, Any]) -> None:
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.active_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def active_id(self) -> str:
        return self._read_active().get("active_id", BASELINE_ID)

    def is_baseline_active(self) -> bool:
        return self.active_id() == BASELINE_ID

    # ------------------------------------------------------------------
    # Variant lifecycle
    # ------------------------------------------------------------------

    def list_variants(self) -> list[MetaVariant]:
        return self.store.list_variants()

    def show(self, variant_id: str) -> MetaVariant | None:
        return self.store.load(variant_id)

    def create_variant(
        self,
        variant_id: str,
        edits: dict[str, Any],
        *,
        parent_variant_id: str = BASELINE_ID,
        notes: str = "",
        archive_role: str = "branch",
    ) -> MetaVariant:
        validate_edits(edits)  # raises SafetyViolation on blocked/unknown keys
        if self.store.exists(variant_id):
            raise FileExistsError(
                f"meta-variant {variant_id!r} already exists; variant ids must be unique"
            )
        if variant_id == BASELINE_ID:
            raise ValueError("'baseline' is reserved")
        variant = MetaVariant(
            variant_id=variant_id,
            parent_variant_id=parent_variant_id,
            edits=dict(edits),
            archive_role=archive_role,
            created_at=utc_iso(),
            promotion_state="candidate",
            notes=notes,
        )
        self.store.write_new(variant)
        self.ledger.append(
            "created",
            variant_id=variant_id,
            parent_variant_id=parent_variant_id,
            payload={"edits": dict(edits), "archive_role": archive_role},
        )
        return variant

    # ------------------------------------------------------------------
    # Canary + validation
    # ------------------------------------------------------------------

    def canary(
        self,
        variant_id: str,
        *,
        baseline_retrieval: RetrievalConfig | None = None,
        baseline_packing: PackingConfig | None = None,
    ) -> CanaryResult:
        """Run the registry's corpus against the variant, record metrics.

        Side effects (all file-local):
          * variant.canary_results is extended with the new metrics (persisted).
          * canary.jsonl in the variant dir is appended.
          * meta-ledger `canary_run` event is appended.
          * If the result's aggregate differs from a baseline canary run
            (same corpus, RetrievalConfig() + PackingConfig() defaults),
            state flips candidate → validated and an extra `validated` event
            lands.
        """
        variant = self.store.load(variant_id)
        if variant is None:
            raise FileNotFoundError(
                f"meta-variant {variant_id!r} not found; create it first"
            )
        baseline_retrieval = baseline_retrieval or RetrievalConfig()
        baseline_packing = baseline_packing or PackingConfig()
        retrieval, packing = apply_variant_edits(
            baseline_retrieval, baseline_packing, variant.edits
        )
        runner = CanaryRunner(self.corpus)
        workspace = self.store.variant_dir(variant_id) / "canary_workspace"
        result = runner.run(variant_id, retrieval, packing, workspace)

        # Baseline reference run (pure defaults, no edits).
        baseline_result = runner.run(
            BASELINE_ID,
            baseline_retrieval,
            baseline_packing,
            workspace.parent / "canary_baseline_workspace",
        )
        result.aggregate["baseline_total_records_returned"] = baseline_result.aggregate[
            "total_records_returned"
        ]
        result.aggregate["baseline_total_packer_chars"] = baseline_result.aggregate[
            "total_packer_chars"
        ]
        result.aggregate["baseline_top1_stability_hash"] = baseline_result.aggregate[
            "top1_stability_hash"
        ]
        result.aggregate["delta_total_records_returned"] = (
            result.aggregate["total_records_returned"]
            - baseline_result.aggregate["total_records_returned"]
        )
        result.aggregate["delta_total_packer_chars"] = (
            result.aggregate["total_packer_chars"]
            - baseline_result.aggregate["total_packer_chars"]
        )
        result.aggregate["differs_from_baseline"] = bool(
            result.aggregate["delta_total_records_returned"]
            or result.aggregate["delta_total_packer_chars"]
            or result.aggregate["top1_stability_hash"]
            != baseline_result.aggregate["top1_stability_hash"]
        )

        # Persist.
        variant.canary_results = list(variant.canary_results) + [dict(result.aggregate)]
        self.store.rewrite_state(variant)
        self.store.append_canary(variant_id, dict(result.aggregate))
        self.ledger.append(
            "canary_run",
            variant_id=variant_id,
            parent_variant_id=variant.parent_variant_id,
            payload=dict(result.aggregate),
        )

        if result.aggregate["differs_from_baseline"] and variant.promotion_state == "candidate":
            variant.promotion_state = "validated"
            self.store.rewrite_state(variant)
            self.ledger.append(
                "validated",
                variant_id=variant_id,
                parent_variant_id=variant.parent_variant_id,
                payload={"reason": "canary delta vs baseline"},
            )

        return result

    # ------------------------------------------------------------------
    # Promote / activate / rollback
    # ------------------------------------------------------------------

    def activate(self, variant_id: str) -> MetaVariant:
        variant = self.store.load(variant_id)
        if variant is None:
            raise FileNotFoundError(f"meta-variant {variant_id!r} not found")
        if variant.promotion_state not in {"validated", "promoted"}:
            raise VariantStateError(
                f"cannot activate variant in state {variant.promotion_state!r}; "
                "must be validated (run canary first) or already promoted"
            )
        # Defense-in-depth: re-validate edits at activation time.
        validate_edits(variant.edits)

        active = self._read_active()
        prior_active = active.get("active_id", BASELINE_ID)
        history = list(active.get("history", []))
        if prior_active != BASELINE_ID and prior_active != variant_id:
            history.append(
                {
                    "variant_id": prior_active,
                    "deactivated_at": utc_iso(),
                }
            )
            # Mark prior variant as deactivated in the ledger for audit.
            self.ledger.append(
                "deactivated",
                variant_id=prior_active,
                parent_variant_id=BASELINE_ID,
                payload={"successor": variant_id},
            )
        active["active_id"] = variant_id
        active["history"] = history
        self._write_active(active)

        if variant.promotion_state != "promoted":
            variant.promotion_state = "promoted"
            variant.promoted_at = utc_iso()
            variant.archive_role = "promoted"
            self.store.rewrite_state(variant)
            self.ledger.append(
                "promoted",
                variant_id=variant_id,
                parent_variant_id=variant.parent_variant_id,
                payload={"from_state": "validated"},
            )
        self.ledger.append(
            "activated",
            variant_id=variant_id,
            parent_variant_id=variant.parent_variant_id,
            payload={"prior_active_id": prior_active},
        )
        return variant

    def rollback(self, variant_id: str) -> MetaVariant:
        variant = self.store.load(variant_id)
        if variant is None:
            raise FileNotFoundError(f"meta-variant {variant_id!r} not found")

        active = self._read_active()
        was_active = active.get("active_id") == variant_id
        if was_active:
            # Restore the most recent non-baseline entry in history if any; else baseline.
            history = list(active.get("history", []))
            while history:
                prev = history.pop()
                candidate = self.store.load(prev.get("variant_id", ""))
                if candidate and candidate.promotion_state == "promoted":
                    active["active_id"] = candidate.variant_id
                    active["history"] = history
                    self._write_active(active)
                    break
            else:
                active["active_id"] = BASELINE_ID
                active["history"] = []
                self._write_active(active)

        variant.promotion_state = "rolled_back"
        variant.rolled_back_at = utc_iso()
        self.store.rewrite_state(variant)
        self.ledger.append(
            "rolled_back",
            variant_id=variant_id,
            parent_variant_id=variant.parent_variant_id,
            payload={"was_active": was_active},
        )
        return variant

    # ------------------------------------------------------------------
    # Overlay application (runtime-boot hook)
    # ------------------------------------------------------------------

    def apply_active_overlay(
        self,
        baseline_retrieval: RetrievalConfig | None = None,
        baseline_packing: PackingConfig | None = None,
    ) -> ActiveOverlay:
        """Return overlaid configs for the currently active variant.

        If no variant is active (or the active variant is missing, corrupt,
        or rolled_back), return baseline unchanged. Failure to apply a
        supposedly-active variant is recorded in the meta-ledger but never
        raises — boot must not crash on a stale pointer.
        """
        baseline_retrieval = baseline_retrieval or RetrievalConfig()
        baseline_packing = baseline_packing or PackingConfig()
        active_id = self.active_id()
        if active_id == BASELINE_ID:
            return ActiveOverlay(
                active_id=BASELINE_ID,
                retrieval=baseline_retrieval,
                packing=baseline_packing,
            )
        variant = self.store.load(active_id)
        if variant is None or variant.promotion_state == "rolled_back":
            # Stale pointer — roll the world back to baseline for safety.
            self._write_active({"active_id": BASELINE_ID, "history": []})
            self.ledger.append(
                "deactivated",
                variant_id=active_id,
                parent_variant_id=BASELINE_ID,
                payload={"reason": "stale_pointer"},
            )
            return ActiveOverlay(
                active_id=BASELINE_ID,
                retrieval=baseline_retrieval,
                packing=baseline_packing,
            )
        try:
            retrieval, packing = apply_variant_edits(
                baseline_retrieval, baseline_packing, variant.edits
            )
        except SafetyViolation:
            # The variant was edited outside the allow-list since creation —
            # refuse to apply and fall back to baseline.
            self._write_active({"active_id": BASELINE_ID, "history": []})
            self.ledger.append(
                "deactivated",
                variant_id=active_id,
                parent_variant_id=BASELINE_ID,
                payload={"reason": "safety_violation_on_apply"},
            )
            return ActiveOverlay(
                active_id=BASELINE_ID,
                retrieval=baseline_retrieval,
                packing=baseline_packing,
            )
        return ActiveOverlay(
            active_id=active_id, retrieval=retrieval, packing=packing
        )
