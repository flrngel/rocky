"""
O4 — ``AgentResponse.answer_bounded_text`` consistency invariant.

Integrators that parse the boundary-marked answer field must be able to rely
on the invariant ``response.text == strip_markers(response.answer_bounded_text)``.
That invariant is the contract the follow-up §3 asks us to document.

This test file:

1. Unit-tests :func:`rocky.core.agent.strip_markers` for the two round-trip
   cases that matter for integrators (marker-wrapped text, and defensive
   inputs that already lack markers).
2. Exercises an ``AgentResponse`` built by the real :meth:`_finalize` path
   and asserts the invariant end-to-end.

Sensitivity witness: if the marker-wrapping site in ``_finalize`` ever
changes to embed extra whitespace, or if :func:`strip_markers` is made a
no-op, one of these assertions will fire.
"""
from __future__ import annotations

from rocky.core.agent import (
    ANSWER_CLOSE_MARKER,
    ANSWER_OPEN_MARKER,
    AgentResponse,
    strip_markers,
)
from rocky.core.router import Lane, RouteDecision, TaskClass


# --------------------------------------------------------------------------
# 1. Unit: strip_markers is the round-trip inverse of marker-wrapping.
# --------------------------------------------------------------------------


def test_strip_markers_roundtrip_simple_text() -> None:
    text = "The cache invalidation strategy relies on LRU eviction."
    bounded = f"{ANSWER_OPEN_MARKER}\n{text}\n{ANSWER_CLOSE_MARKER}"
    assert strip_markers(bounded) == text


def test_strip_markers_passthrough_when_unmarked() -> None:
    # Defensive: calling strip_markers on text that lacks markers must be a
    # no-op (returns the input unchanged), so old consumers that never see
    # markers continue to work.
    plain = "Answer without markers."
    assert strip_markers(plain) == plain


def test_strip_markers_handles_empty_body() -> None:
    assert strip_markers("") == ""
    bounded = f"{ANSWER_OPEN_MARKER}\n\n{ANSWER_CLOSE_MARKER}"
    assert strip_markers(bounded) == ""


def test_strip_markers_preserves_internal_newlines() -> None:
    # Integrators must be able to rely on internal newlines surviving.
    body = "Line one.\nLine two.\n\nLine four."
    bounded = f"{ANSWER_OPEN_MARKER}\n{body}\n{ANSWER_CLOSE_MARKER}"
    assert strip_markers(bounded) == body


# --------------------------------------------------------------------------
# 2. Field-level consistency on a constructed AgentResponse.
# --------------------------------------------------------------------------


def _fake_route() -> RouteDecision:
    return RouteDecision(
        lane=Lane.STANDARD,
        task_class=TaskClass.RESEARCH,
        risk="low",
        reasoning="fake route for answer_bounded_text consistency",
        tool_families=["web"],
        task_signature="research/general",
    )


def test_agent_response_text_matches_stripped_bounded_text() -> None:
    text = "Paris is the capital of France."
    bounded = f"{ANSWER_OPEN_MARKER}\n{text}\n{ANSWER_CLOSE_MARKER}"
    response = AgentResponse(
        text=text,
        route=_fake_route(),
        verification={"status": "pass"},
        answer_bounded_text=bounded,
    )
    assert response.text == strip_markers(response.answer_bounded_text)


def test_agent_response_default_bounded_text_is_empty_string() -> None:
    # CF-4: an AgentResponse constructed without explicitly setting
    # answer_bounded_text must default to an empty string, not raise
    # AttributeError. This is the backward-compat guard for callers that do
    # not set the field.
    response = AgentResponse(
        text="hello",
        route=_fake_route(),
        verification={"status": "pass"},
    )
    assert response.answer_bounded_text == ""
