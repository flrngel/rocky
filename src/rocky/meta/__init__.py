"""Bounded meta-learning archive (PRD Phase 3).

Meta-variants are versioned configuration overlays on top of the learning
substrate (retrieval weights, packer budgets, etc.). Every variant is stored
append-only under `.rocky/meta/variants/<variant_id>/`, gated through a
safety allow-list, and promoted only after passing an offline canary.

Public surface:
  - `MetaVariant` — dataclass schema.
  - `MetaVariantStore` — per-variant directory manager.
  - `MetaVariantRegistry` — glue that exposes list/canary/promote/rollback/activate.
  - `SafetyViolation` — raised when an edit touches a blocked key.
  - `CanaryRunner`, `CanaryCorpus`, `CanaryResult` — offline replay engine.
  - `apply_variant_edits` — pure-function overlay (takes baseline configs, returns
    overlaid configs).
"""

from __future__ import annotations

from rocky.meta.canary import (
    CanaryCorpus,
    CanaryResult,
    CanaryRunner,
    CanaryTask,
    default_corpus,
)
from rocky.meta.ledger import MetaLedger, MetaLedgerEvent
from rocky.meta.overlay import apply_variant_edits
from rocky.meta.registry import ActiveOverlay, MetaVariantRegistry
from rocky.meta.safety import (
    ALLOWED_KEYS,
    BLOCKED_KEY_PREFIXES,
    SafetyViolation,
    validate_edits,
)
from rocky.meta.variants import MetaVariant, MetaVariantStore

__all__ = [
    "ActiveOverlay",
    "ALLOWED_KEYS",
    "BLOCKED_KEY_PREFIXES",
    "CanaryCorpus",
    "CanaryResult",
    "CanaryRunner",
    "CanaryTask",
    "MetaLedger",
    "MetaLedgerEvent",
    "MetaVariant",
    "MetaVariantRegistry",
    "MetaVariantStore",
    "SafetyViolation",
    "apply_variant_edits",
    "default_corpus",
    "validate_edits",
]
