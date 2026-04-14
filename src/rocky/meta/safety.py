"""Safety allow-list for meta-variant edits (PRD §21.1 rule 2).

Meta-variants may only edit a narrow, explicitly enumerated surface of the
learning configuration. Anything that could weaken a security boundary
(permissions, providers, tool timeouts, freeze/bypass toggles, or the
learning-enabled master switch) is blocked at three sites:

    1. variant creation (`MetaVariantRegistry.create_variant`)
    2. variant activation (`MetaVariantRegistry.activate`)
    3. overlay application (`rocky.meta.overlay.apply_variant_edits`)

Defense in depth — a leak past one gate is still caught by the next.
"""

from __future__ import annotations


class SafetyViolation(Exception):
    """Raised when a meta-variant edit targets a blocked key."""

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"SafetyViolation on key '{key}': {reason}")
        self.key = key
        self.reason = reason


# Editable (allow-listed) top-level keys. Each entry is either an exact dotted
# path or a prefix followed by '.*' meaning "any leaf under this subtree".
ALLOWED_KEYS: tuple[str, ...] = (
    "retrieval.top_k_limit",
    "retrieval.authority_weight.*",
    "retrieval.promotion_weight.*",
    "retrieval.ts_exact_score",
    "retrieval.ts_prefix_score",
    "retrieval.tf_score",
    "retrieval.thread_relevance_cap",
    "retrieval.prompt_overlap_cap",
    "retrieval.prompt_overlap_multiplier",
    "retrieval.trigger_literal_score",
    "retrieval.fc_score",
    "retrieval.evidence_quality_cap",
    "retrieval.recency_score",
    "retrieval.conflict_status_score",
    "retrieval.prior_success_cap",
    "retrieval.require_signal",
    "packing.workspace_brief_budget",
    "packing.retrospective_body_budget",
    "packing.student_profile_budget",
    "packing.legacy_note_budget",
    "packing.hard_lines_cap",
    "packing.procedural_cap",
    "packing.retro_cap",
    "packing.style_cue_cap",
    "packing.repeat_step_cap",
    "packing.avoid_step_cap",
    "packing.workflow_step_body_budget",
    "packing.user_prompt_budget",
)

# Hard-blocked prefixes — any edit whose key begins with any of these is a
# `SafetyViolation` regardless of the allow-list. Gives belt-and-suspenders
# protection against future ALLOWED_KEYS additions that might accidentally
# shadow a security-sensitive key.
BLOCKED_KEY_PREFIXES: tuple[str, ...] = (
    "permissions.",
    "providers.",
    "tools.shell_timeout_s",
    "tools.python_timeout_s",
    "learning.enabled",
    "learning.slow_learner_enabled",
    "learning.auto_publish_project_skills",
    "learning.auto_self_reflection_enabled",
    "freeze.",
    "freeze_mode",
    "bypass.",
    "active_provider",
)

# Integer/float bounds for allowlisted keys. Prevents absurd values from
# passing validation even when the key itself is allowed.
_BOUNDS: dict[str, tuple[float, float]] = {
    "retrieval.top_k_limit": (1, 20),
    "retrieval.ts_exact_score": (0.0, 20.0),
    "retrieval.ts_prefix_score": (0.0, 20.0),
    "retrieval.tf_score": (0.0, 20.0),
    "retrieval.thread_relevance_cap": (0, 20),
    "retrieval.prompt_overlap_cap": (0, 20),
    "retrieval.prompt_overlap_multiplier": (0.0, 10.0),
    "retrieval.trigger_literal_score": (0.0, 20.0),
    "retrieval.fc_score": (0.0, 20.0),
    "retrieval.evidence_quality_cap": (0, 20),
    "retrieval.recency_score": (0.0, 10.0),
    "retrieval.conflict_status_score": (-10.0, 10.0),
    "retrieval.prior_success_cap": (0, 20),
    "packing.workspace_brief_budget": (200, 8000),
    "packing.retrospective_body_budget": (100, 2000),
    "packing.student_profile_budget": (200, 8000),
    "packing.legacy_note_budget": (200, 8000),
    "packing.hard_lines_cap": (1, 40),
    "packing.procedural_cap": (1, 20),
    "packing.retro_cap": (1, 10),
    "packing.style_cue_cap": (1, 10),
    "packing.repeat_step_cap": (1, 20),
    "packing.avoid_step_cap": (1, 20),
    "packing.workflow_step_body_budget": (40, 2000),
    "packing.user_prompt_budget": (200, 8000),
}


# Bounds for the weight-subtree leaves. These prevent a meta-variant from
# setting an absurd weight (e.g. teacher=999999) that would silently dominate
# every retrieval decision. The weights are still tunable inside a sensible
# envelope; the bounds are wide enough for legitimate experimentation.
_WEIGHT_SUBTREE_BOUNDS: tuple[tuple[str, tuple[float, float]], ...] = (
    ("retrieval.authority_weight.", (-10, 10)),
    ("retrieval.promotion_weight.", (-10, 10)),
)


def _weight_bounds_for(key: str) -> tuple[float, float] | None:
    for prefix, bounds in _WEIGHT_SUBTREE_BOUNDS:
        if key.startswith(prefix):
            return bounds
    return None


def _is_blocked(key: str) -> bool:
    for prefix in BLOCKED_KEY_PREFIXES:
        if key == prefix.rstrip(".") or key.startswith(prefix):
            return True
    return False


def _is_allowed(key: str) -> bool:
    for pattern in ALLOWED_KEYS:
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            if key.startswith(prefix + "."):
                remainder = key[len(prefix) + 1 :]
                # exactly one leaf below the prefix
                if remainder and "." not in remainder:
                    return True
        elif key == pattern:
            return True
    return False


def validate_edits(edits: dict[str, object]) -> None:
    """Raise `SafetyViolation` on the first offending key.

    Rules:
      1. `edits` must be a dict of dotted-path strings → scalar (or small list for
         weight subtrees — rejected here for now; only leaf values allowed).
      2. Any key prefixed by a `BLOCKED_KEY_PREFIXES` entry fails.
      3. Any key not matching an `ALLOWED_KEYS` pattern fails.
      4. Integer/float values outside configured `_BOUNDS` fail.
    """

    if not isinstance(edits, dict):
        raise SafetyViolation("<root>", "edits payload must be a dict")

    for key, value in edits.items():
        if not isinstance(key, str) or not key:
            raise SafetyViolation(str(key), "edit keys must be non-empty strings")
        if _is_blocked(key):
            raise SafetyViolation(
                key,
                "key is in the BLOCKED_KEY_PREFIXES safety allow-list",
            )
        if not _is_allowed(key):
            raise SafetyViolation(
                key,
                "key is not in the ALLOWED_KEYS meta-variant whitelist",
            )
        if isinstance(value, (dict, list, tuple, set)):
            raise SafetyViolation(
                key, "edit values must be scalars (int / float / bool / str)"
            )
        if key in _BOUNDS and isinstance(value, (int, float)) and not isinstance(value, bool):
            low, high = _BOUNDS[key]
            if value < low or value > high:
                raise SafetyViolation(
                    key, f"value {value} outside allowed bounds [{low}, {high}]"
                )
        weight_bounds = _weight_bounds_for(key)
        if weight_bounds and isinstance(value, (int, float)) and not isinstance(value, bool):
            low, high = weight_bounds
            if value < low or value > high:
                raise SafetyViolation(
                    key, f"weight {value} outside allowed bounds [{low}, {high}]"
                )
