"""Pure-function overlay: apply a safety-validated set of edits to baseline configs.

Called at two sites:
  - `rocky.meta.registry.MetaVariantRegistry.apply_active_overlay` — on runtime boot.
  - `rocky.meta.canary.CanaryRunner.run` — to exercise a variant without activating it.

Behavior:
  - An empty edits dict returns the input configs unchanged (identity).
  - Every edit is re-validated through `validate_edits` as defense in depth.
  - Unknown retrieval/packing fields raise `SafetyViolation` — we do not silently
    ignore nonsense keys (would mask safety-allow-list drift).
"""

from __future__ import annotations

import copy
from typing import Any

from rocky.config.models import PackingConfig, RetrievalConfig
from rocky.meta.safety import SafetyViolation, validate_edits


def _set_scoped(
    retrieval: RetrievalConfig,
    packing: PackingConfig,
    key: str,
    value: Any,
) -> None:
    prefix, _, tail = key.partition(".")
    if prefix == "retrieval":
        _apply_to_config(retrieval, tail, value)
    elif prefix == "packing":
        _apply_to_config(packing, tail, value)
    else:  # pragma: no cover — already rejected by validate_edits
        raise SafetyViolation(
            key, f"overlay target must be 'retrieval' or 'packing' (got {prefix!r})"
        )


def _apply_to_config(target: Any, field_path: str, value: Any) -> None:
    head, _, tail = field_path.partition(".")
    if not hasattr(target, head):
        raise SafetyViolation(
            f"{type(target).__name__.lower()}.{field_path}",
            f"unknown field {head!r} on {type(target).__name__}",
        )
    if tail:
        subtarget = getattr(target, head)
        if not isinstance(subtarget, dict):
            raise SafetyViolation(
                f"{type(target).__name__.lower()}.{field_path}",
                "nested overlay only supported on dict-typed fields",
            )
        # Write to a copy so baseline dict does not mutate.
        new_dict = dict(subtarget)
        new_dict[tail] = value
        setattr(target, head, new_dict)
    else:
        setattr(target, head, value)


def apply_variant_edits(
    retrieval: RetrievalConfig,
    packing: PackingConfig,
    edits: dict[str, Any],
) -> tuple[RetrievalConfig, PackingConfig]:
    """Return new (RetrievalConfig, PackingConfig) instances with edits applied.

    Inputs are never mutated; the overlay is produced by deep-copying the
    baseline and setting overlaid fields on the copies.
    """
    validate_edits(edits)
    overlaid_retrieval = copy.deepcopy(retrieval)
    overlaid_packing = copy.deepcopy(packing)
    for key, value in edits.items():
        _set_scoped(overlaid_retrieval, overlaid_packing, key, value)
    return overlaid_retrieval, overlaid_packing
