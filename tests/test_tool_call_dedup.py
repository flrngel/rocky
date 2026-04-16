"""Tests for O11 — per-turn tool-call dedup and loop-guard injection.

The dedup logic lives in the module-level helper
``rocky.core.agent._maybe_cached_tool_call`` which is unit-testable in
isolation.  A structural guard confirms the sentinel string is present
inside AgentCore.run source.
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from rocky.core.agent import (
    AgentCore,
    _LOOP_GUARD_SENTINEL,
    _LOOP_GUARD_THRESHOLD,
    _args_hash,
    _maybe_cached_tool_call,
)
from rocky.core.messages import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state() -> tuple[
    dict[tuple[str, str], str],
    dict[tuple[str, str], int],
    set[tuple[str, str]],
    list[Any],
]:
    """Return fresh (cache, hits, guard_emitted, messages) state."""
    return {}, {}, set(), []


def _constant_dispatch(result: str = "ok"):
    """Return a dispatch function that always yields *result*."""
    calls: list[tuple[str, dict]] = []

    def dispatch(name: str, arguments: dict[str, Any]) -> str:
        calls.append((name, arguments))
        return result

    dispatch.calls = calls  # type: ignore[attr-defined]
    return dispatch


# ---------------------------------------------------------------------------
# Test 1 — 2nd identical call is short-circuited (cache hit)
# ---------------------------------------------------------------------------

def test_second_identical_call_returns_cached():
    cache, hits, guard_emitted, msgs = _make_state()
    dispatch = _constant_dispatch("result-A")

    # First call — real dispatch.
    r1 = _maybe_cached_tool_call(
        tool_call_cache=cache,
        tool_call_hits=hits,
        loop_guard_emitted=guard_emitted,
        messages=msgs,
        name="shell",
        arguments={"cmd": "ls"},
        dispatch_fn=dispatch,
    )
    # Second call — same args, should be cached.
    r2 = _maybe_cached_tool_call(
        tool_call_cache=cache,
        tool_call_hits=hits,
        loop_guard_emitted=guard_emitted,
        messages=msgs,
        name="shell",
        arguments={"cmd": "ls"},
        dispatch_fn=dispatch,
    )

    assert r1 == "result-A"
    assert r2 == "result-A"  # cached
    # Dispatch was called only once.
    assert len(dispatch.calls) == 1


def test_five_identical_calls_dispatch_once():
    cache, hits, guard_emitted, msgs = _make_state()
    dispatch = _constant_dispatch("result-B")

    for _ in range(5):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=guard_emitted,
            messages=msgs,
            name="fetch_url",
            arguments={"url": "https://example.com"},
            dispatch_fn=dispatch,
        )

    assert len(dispatch.calls) == 1  # only the first was real


# ---------------------------------------------------------------------------
# Test 2 — Structural guard: sentinel string in AgentCore.run source
# ---------------------------------------------------------------------------

def test_loop_guard_wired_in_agent_run():
    """The dedup helper (which embeds the sentinel) must be called from run()."""
    src = inspect.getsource(AgentCore.run)
    # The sentinel lives inside _maybe_cached_tool_call; run() must call it.
    assert "_maybe_cached_tool_call" in src, (
        "Expected _maybe_cached_tool_call (which embeds the loop-guard sentinel) "
        "to be called from AgentCore.run"
    )
    # Also confirm the sentinel constant itself exists at module level.
    assert _LOOP_GUARD_SENTINEL == "[rocky-loop-guard]"


# ---------------------------------------------------------------------------
# Test 3 — Distinct args bypass the cache (different hash -> real dispatch)
# ---------------------------------------------------------------------------

def test_distinct_args_each_dispatch_independently():
    cache, hits, guard_emitted, msgs = _make_state()
    call_log: list[str] = []

    def dispatch(name: str, arguments: dict[str, Any]) -> str:
        result = f"result-{arguments['n']}"
        call_log.append(result)
        return result

    r1 = _maybe_cached_tool_call(
        tool_call_cache=cache,
        tool_call_hits=hits,
        loop_guard_emitted=guard_emitted,
        messages=msgs,
        name="shell",
        arguments={"n": 1},
        dispatch_fn=dispatch,
    )
    r2 = _maybe_cached_tool_call(
        tool_call_cache=cache,
        tool_call_hits=hits,
        loop_guard_emitted=guard_emitted,
        messages=msgs,
        name="shell",
        arguments={"n": 2},
        dispatch_fn=dispatch,
    )

    assert r1 == "result-1"
    assert r2 == "result-2"
    assert call_log == ["result-1", "result-2"]  # both dispatched


# ---------------------------------------------------------------------------
# Test 4 — Cross-turn isolation: fresh cache = fresh dispatch
# ---------------------------------------------------------------------------

def test_cross_turn_isolation():
    """Using a new cache for the second invocation must trigger real dispatch."""
    dispatch = _constant_dispatch("fresh-result")

    # Turn 1
    cache1, hits1, guard1, msgs1 = _make_state()
    _maybe_cached_tool_call(
        tool_call_cache=cache1,
        tool_call_hits=hits1,
        loop_guard_emitted=guard1,
        messages=msgs1,
        name="shell",
        arguments={"cmd": "pwd"},
        dispatch_fn=dispatch,
    )
    assert len(dispatch.calls) == 1

    # Turn 2 — completely fresh state (simulates new run() call).
    cache2, hits2, guard2, msgs2 = _make_state()
    _maybe_cached_tool_call(
        tool_call_cache=cache2,
        tool_call_hits=hits2,
        loop_guard_emitted=guard2,
        messages=msgs2,
        name="shell",
        arguments={"cmd": "pwd"},
        dispatch_fn=dispatch,
    )
    # Second turn should also have dispatched (total = 2 real calls).
    assert len(dispatch.calls) == 2


# ---------------------------------------------------------------------------
# Test 5 — Loop guard message injection after N identical hits
# ---------------------------------------------------------------------------

def test_loop_guard_injected_after_threshold():
    cache, hits, guard_emitted, msgs = _make_state()
    dispatch = _constant_dispatch("same-result")

    for _ in range(_LOOP_GUARD_THRESHOLD + 1):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=guard_emitted,
            messages=msgs,
            name="shell",
            arguments={"cmd": "ls"},
            dispatch_fn=dispatch,
        )

    guard_messages = [
        m for m in msgs
        if isinstance(m, Message) and _LOOP_GUARD_SENTINEL in (m.content or "")
    ]
    assert len(guard_messages) == 1, (
        f"Expected exactly one guard message, got {len(guard_messages)}: {guard_messages}"
    )
    assert "shell" in guard_messages[0].content
    assert str(_LOOP_GUARD_THRESHOLD) in guard_messages[0].content


def test_loop_guard_injected_only_once_per_key():
    """Guard message must not be duplicated even with 10 identical calls."""
    cache, hits, guard_emitted, msgs = _make_state()
    dispatch = _constant_dispatch("same-result")

    for _ in range(10):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=guard_emitted,
            messages=msgs,
            name="shell",
            arguments={"cmd": "ls"},
            dispatch_fn=dispatch,
        )

    guard_messages = [
        m for m in msgs
        if isinstance(m, Message) and _LOOP_GUARD_SENTINEL in (m.content or "")
    ]
    assert len(guard_messages) == 1, "Guard should only be injected once per key"


def test_loop_guard_not_injected_below_threshold():
    """No guard message if hits stay below threshold."""
    cache, hits, guard_emitted, msgs = _make_state()
    dispatch = _constant_dispatch("same-result")

    # Call exactly threshold - 1 times total (1 real + threshold-2 cached).
    for _ in range(_LOOP_GUARD_THRESHOLD - 1):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=guard_emitted,
            messages=msgs,
            name="shell",
            arguments={"cmd": "ls"},
            dispatch_fn=dispatch,
        )

    guard_messages = [
        m for m in msgs
        if isinstance(m, Message) and _LOOP_GUARD_SENTINEL in (m.content or "")
    ]
    assert len(guard_messages) == 0, "Guard should not fire below threshold"


# ---------------------------------------------------------------------------
# Test 6 — args_hash stability
# ---------------------------------------------------------------------------

def test_args_hash_same_dict_produces_same_hash():
    h1 = _args_hash({"a": 1, "b": 2})
    h2 = _args_hash({"b": 2, "a": 1})  # different insertion order
    assert h1 == h2


def test_args_hash_different_values_produce_different_hash():
    h1 = _args_hash({"cmd": "ls"})
    h2 = _args_hash({"cmd": "pwd"})
    assert h1 != h2
