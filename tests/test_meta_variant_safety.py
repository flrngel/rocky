"""Phase 3 T-META-3 — Safety allow-list + adversarial rejection.

Covers SC-SAFETY (A6, A7):
  * Every key under a BLOCKED_KEY_PREFIXES prefix raises SafetyViolation.
  * Keys outside ALLOWED_KEYS raise SafetyViolation (unknown keys are not
    silently accepted — drift would mask safety-allow-list regressions).
  * Allowed keys accept values within bounds; reject out-of-bound values.
  * Dict/list/tuple values are rejected (scalar-only edits).

Sensitivity witness: removing `permissions.` from BLOCKED_KEY_PREFIXES would
flip test_blocked_permissions_edit to PASS the edit — we assert it fails.
"""

from __future__ import annotations

import pytest

from rocky.meta.safety import (
    ALLOWED_KEYS,
    BLOCKED_KEY_PREFIXES,
    SafetyViolation,
    validate_edits,
)


def test_allow_listed_edit_accepted() -> None:
    validate_edits({"retrieval.top_k_limit": 2})
    validate_edits({"packing.procedural_cap": 3})
    validate_edits({"retrieval.authority_weight.teacher": 5})
    validate_edits({"retrieval.require_signal": False})


def test_empty_edits_accepted() -> None:
    validate_edits({})


def test_non_dict_payload_rejected() -> None:
    with pytest.raises(SafetyViolation) as exc_info:
        validate_edits([("retrieval.top_k_limit", 2)])  # type: ignore[arg-type]
    assert "edits payload must be a dict" in str(exc_info.value)


@pytest.mark.parametrize(
    "blocked_key,value",
    [
        ("permissions.mode", "auto"),
        ("permissions.allow.shell", ["rm"]),  # pylint: disable=invalid-name
        ("providers.openai.base_url", "http://attacker"),
        ("providers.openai.api_key", "stolen"),
        ("tools.shell_timeout_s", 1),
        ("tools.python_timeout_s", 1),
        ("learning.enabled", False),
        ("learning.slow_learner_enabled", True),
        ("freeze.on", True),
        ("freeze_mode", True),
        ("bypass.safety", True),
        ("active_provider", "evil"),
    ],
)
def test_blocked_edits_rejected(blocked_key: str, value: object) -> None:
    with pytest.raises(SafetyViolation) as exc_info:
        validate_edits({blocked_key: value})
    assert exc_info.value.key == blocked_key
    assert "BLOCKED_KEY_PREFIXES" in exc_info.value.reason


def test_unknown_allow_listed_key_rejected() -> None:
    with pytest.raises(SafetyViolation) as exc_info:
        validate_edits({"retrieval.unknown_future_knob": 7})
    assert "ALLOWED_KEYS" in exc_info.value.reason


def test_out_of_bound_scalar_rejected() -> None:
    with pytest.raises(SafetyViolation) as exc_info:
        validate_edits({"retrieval.top_k_limit": 999})
    assert "bounds" in exc_info.value.reason
    with pytest.raises(SafetyViolation):
        validate_edits({"packing.procedural_cap": 0})


def test_nested_container_values_rejected() -> None:
    # Even an allow-listed *path* must have a scalar value.
    with pytest.raises(SafetyViolation):
        validate_edits({"retrieval.top_k_limit": [2]})  # type: ignore[arg-type]
    with pytest.raises(SafetyViolation):
        validate_edits({"packing.procedural_cap": {"inner": 1}})  # type: ignore[arg-type]


def test_blocked_prefix_list_is_nonempty() -> None:
    assert len(BLOCKED_KEY_PREFIXES) >= 8, (
        "safety allow-list must block all PRD §21.1 rule 2 categories; "
        "shrinking this list below 8 entries signals regression"
    )


def test_weight_subtree_bounds_enforced() -> None:
    # Reasonable weight values pass.
    validate_edits({"retrieval.authority_weight.teacher": 5})
    validate_edits({"retrieval.promotion_weight.candidate": -3})
    # Out-of-bound weight rejected even though the key is allow-listed.
    with pytest.raises(SafetyViolation) as exc_info:
        validate_edits({"retrieval.authority_weight.teacher": 99999})
    assert "outside allowed bounds" in exc_info.value.reason
    with pytest.raises(SafetyViolation):
        validate_edits({"retrieval.promotion_weight.promoted": -100})


def test_allowed_keys_are_scoped() -> None:
    """Every ALLOWED_KEYS entry must target retrieval.* or packing.* only."""
    for pattern in ALLOWED_KEYS:
        scope = pattern.split(".", 1)[0]
        assert scope in {"retrieval", "packing"}, (
            f"ALLOWED_KEYS leaked outside retrieval/packing: {pattern!r}"
        )
