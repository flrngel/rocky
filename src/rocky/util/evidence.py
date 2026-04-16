"""Shared lexical evidence matcher.

Used by:
  - O5 (retrospective evidence grounding in app.py::_auto_self_reflect):
    filter retro.evidence so only citations whose tokens overlap with at least
    one non-empty tool event payload are kept.
  - O6 (semantic research verifier in core/verifiers.py):
    for each factual claim in a research-mode answer, check overlap against
    fetch_url / search_web payloads; claims with zero overlap are surfaced as
    unsupported.

The matcher is pure (no I/O, no LLM). It uses stdlib only.
"""
from __future__ import annotations

from typing import Iterable, Sequence, Any

_TOKEN_SPLIT_RE = None  # lazily compiled


def _tokens(text: str) -> set[str]:
    import re
    global _TOKEN_SPLIT_RE
    if _TOKEN_SPLIT_RE is None:
        _TOKEN_SPLIT_RE = re.compile(r"[A-Za-z0-9_./:-]+")
    return {t.lower() for t in _TOKEN_SPLIT_RE.findall(text) if t}


def _payload_text(event: Any) -> str:
    """Normalize a tool event into a single string of its payload.

    Uses rocky.tool_events.tool_event_payload if available; falls back to a
    best-effort extraction that never raises.
    """
    try:
        from rocky.tool_events import tool_event_payload
    except Exception:  # pragma: no cover - defensive
        tool_event_payload = None  # type: ignore[assignment]
    if tool_event_payload is not None:
        try:
            payload = tool_event_payload(event)
            if isinstance(payload, str):
                return payload
            if payload is None:
                return ""
            # Fall through to generic serialization
        except Exception:
            payload = None
    # Generic fallback: stringify event fields that are likely to contain text
    if isinstance(event, dict):
        parts: list[str] = []
        for key in ("stdout", "stderr", "output", "content", "text", "body"):
            value = event.get(key)
            if isinstance(value, str):
                parts.append(value)
        if not parts:
            # Last resort: full repr without raising
            try:
                parts.append(str(event))
            except Exception:
                pass
        return "\n".join(parts)
    if isinstance(event, str):
        return event
    try:
        return str(event)
    except Exception:
        return ""


def ground_evidence_citations(
    items: Sequence[str] | None,
    tool_events: Iterable[Any] | None,
    *,
    direction: str = "retro",
    min_overlap: int = 1,
) -> list[str]:
    """Filter ``items`` (evidence citations or claims) against ``tool_events``.

    An item is kept if its token set shares at least ``min_overlap`` tokens
    with at least one non-empty tool event payload.

    Args:
        items: sequence of citation/claim strings. None -> [].
        tool_events: iterable of tool event dicts/objects. None -> [].
        direction: "retro" for citations, "claim" for answer claims.
            Only used for logging/metrics (no behavior difference).
        min_overlap: minimum number of overlapping lowercase tokens required
            for an item to be kept. Defaults to 1.

    Returns:
        Filtered list of items (subset of input, preserving order).
    """
    if not items:
        return []
    if direction not in ("retro", "claim"):
        raise ValueError(f"direction must be 'retro' or 'claim', got {direction!r}")

    # Pre-compute payload token sets; skip empty payloads.
    payload_token_sets: list[set[str]] = []
    if tool_events:
        for event in tool_events:
            payload = _payload_text(event)
            if not payload.strip():
                continue
            tokens = _tokens(payload)
            if tokens:
                payload_token_sets.append(tokens)

    if not payload_token_sets:
        return []  # No grounded evidence available -> drop all items.

    kept: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        item_tokens = _tokens(item)
        if not item_tokens:
            continue
        for payload_tokens in payload_token_sets:
            if len(item_tokens & payload_tokens) >= min_overlap:
                kept.append(item)
                break
    return kept


__all__ = ["ground_evidence_citations"]
