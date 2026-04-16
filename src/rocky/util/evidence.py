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

    O2: This function used to lazy-import
    ``rocky.tool_events.tool_event_payload`` from within the function body to
    avoid a module-load-time import cycle (``util/`` → ``tool_events`` →
    ``tools/base``). The lazy import was invisible to static analysis and
    would fail at runtime if ``util/`` were ever extracted to its own package.
    We now cover both the modern tool-event shape (``raw_text`` field produced
    by :func:`rocky.tool_events.normalize_tool_result_event`) and the legacy
    shape (``stdout``/``stderr``/``output``/``content``/``text``/``body``
    dict keys) directly, without any import of ``rocky.tool_events``.

    Never raises; always returns a string (possibly empty).
    """
    if isinstance(event, dict):
        # Modern shape: payloads normalized via normalize_tool_result_event
        # expose the canonical serialized payload under ``raw_text``. If it is
        # JSON, return the structured string so downstream tokenization picks
        # up field names as well as values.
        raw_text = event.get("raw_text")
        if isinstance(raw_text, str) and raw_text:
            return raw_text
        # Legacy shape: capture every string-valued text channel we know about.
        parts: list[str] = []
        for key in ("stdout", "stderr", "output", "content", "text", "body", "model_text", "summary_text"):
            value = event.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
        if parts:
            return "\n".join(parts)
        # Last resort: safely serialize the whole dict.
        try:
            return str(event)
        except Exception:
            return ""
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
