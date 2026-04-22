"""
status: DONE
task: O11

Tests for O11 — per-turn tool-call dedup and loop-guard injection.

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
)  # noqa: F401 — AgentCore used by A4 source-inspection tests below
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


# ---------------------------------------------------------------------------
# O18 — loop-guard counter telemetry
# ---------------------------------------------------------------------------


def test_loop_guard_counter_increments_on_injection() -> None:
    """When the loop-guard fires, the provided counter list[0] should increment.
    Each distinct (name, args) key only injects once per turn, so the counter
    equals the number of distinct keys that crossed the threshold."""
    cache, hits, emitted, messages = _make_state()
    counter: list[int] = [0]
    dispatch = _constant_dispatch("same")

    # First 3 calls: one real + two cached hits -> guard fires exactly once
    # (the third call crosses the threshold).
    for _ in range(_LOOP_GUARD_THRESHOLD):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=emitted,
            messages=messages,
            name="list_files",
            arguments={"path": "."},
            dispatch_fn=dispatch,
            loop_guard_counter=counter,
        )

    assert counter[0] == 1, (
        f"Expected counter to be 1 after threshold crossing; got {counter[0]}"
    )
    assert any(
        isinstance(m.content, str) and _LOOP_GUARD_SENTINEL in m.content
        for m in messages
    )


def test_loop_guard_counter_does_not_double_count_same_key() -> None:
    """Subsequent identical calls after the guard has fired must NOT increment
    the counter a second time — the guard is once-per-key per turn."""
    cache, hits, emitted, messages = _make_state()
    counter: list[int] = [0]
    dispatch = _constant_dispatch("same")

    for _ in range(_LOOP_GUARD_THRESHOLD + 3):  # cross threshold, then keep calling
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=emitted,
            messages=messages,
            name="list_files",
            arguments={"path": "."},
            dispatch_fn=dispatch,
            loop_guard_counter=counter,
        )

    assert counter[0] == 1, (
        f"Expected counter to stay at 1 for a single recurring key; got {counter[0]}"
    )


def test_loop_guard_counter_is_optional() -> None:
    """CF-4: callers that don't pass a counter must still get guard injection
    without error (counter kwarg is default None)."""
    cache, hits, emitted, messages = _make_state()
    dispatch = _constant_dispatch("same")

    for _ in range(_LOOP_GUARD_THRESHOLD):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=emitted,
            messages=messages,
            name="list_files",
            arguments={"path": "."},
            dispatch_fn=dispatch,
        )
    # Guard must still fire even without a counter argument.
    assert any(
        isinstance(m.content, str) and _LOOP_GUARD_SENTINEL in m.content
        for m in messages
    )


def test_loop_guard_counter_zero_when_no_repeats() -> None:
    """No repeated calls -> counter stays 0 (CF-4)."""
    cache, hits, emitted, messages = _make_state()
    counter: list[int] = [0]
    dispatch = _constant_dispatch("unique")

    for i in range(5):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=emitted,
            messages=messages,
            name="list_files",
            arguments={"path": f"/unique/{i}"},
            dispatch_fn=dispatch,
            loop_guard_counter=counter,
        )
    assert counter[0] == 0


# ---------------------------------------------------------------------------
# A4 follow-up — Repair-path loop-guard counter invariants (review S1 fix).
# Three tests locking:
#   (1) AgentCore.__init__ initializes _last_repair_loop_guard_hits to 0.
#   (2) _maybe_cached_tool_call increments the loop_guard_counter after the
#       threshold — this is exactly the mechanism the repair path relies on.
#   (3) AgentCore.run resets _last_repair_loop_guard_hits at entry so a prior
#       turn's count does not leak into this turn's trace.
# ---------------------------------------------------------------------------


def test_last_repair_loop_guard_hits_initializes_to_zero() -> None:
    """AgentCore must carry ``_last_repair_loop_guard_hits`` as an int-field
    initialized to 0. Without the init line, the repair-path carry-field is
    undefined until ``run()`` is entered, breaking the S1 aggregation
    contract that rolls the repair-path counter into ``trace["loop_guard_hits"]``.
    """
    src = inspect.getsource(AgentCore.__init__)
    assert "self._last_repair_loop_guard_hits" in src and "= 0" in src, (
        "AgentCore.__init__ must initialize _last_repair_loop_guard_hits = 0."
    )


def test_cached_tool_call_accumulates_loop_guard_counter() -> None:
    """The repair path's ``loop_guard_counter`` is exactly the mechanism that
    ``_maybe_cached_tool_call`` increments past ``_LOOP_GUARD_THRESHOLD``.
    Directly witness that the counter kwarg is honored on identical repeated
    calls — this is what the repair path depends on for S1 aggregation."""
    cache, hits, guard_emitted, messages = _make_state()
    dispatch = _constant_dispatch("ok")
    counter = [0]

    # _LOOP_GUARD_THRESHOLD is 3, so call 4× with the same (name, args)
    # to guarantee the guard fires.
    for _ in range(4):
        _maybe_cached_tool_call(
            tool_call_cache=cache,
            tool_call_hits=hits,
            loop_guard_emitted=guard_emitted,
            messages=messages,
            name="read_file",
            arguments={"path": "a.txt"},
            dispatch_fn=dispatch,
            loop_guard_counter=counter,
        )

    assert counter[0] >= 1, (
        "loop_guard_counter must be incremented at least once after "
        f"_LOOP_GUARD_THRESHOLD ({_LOOP_GUARD_THRESHOLD}) identical calls."
    )


def test_agent_core_run_resets_repair_loop_guard_hits_at_entry() -> None:
    """Review S1 requires ``run()`` to zero ``_last_repair_loop_guard_hits``
    at entry so a prior turn's count does not leak into this turn's trace.
    Lint-level source witness per durable lesson L1 (invariants as tests);
    avoids the construction cost of a full ``run()`` invocation just to
    prove that a single reset line lives in the right method."""
    src = inspect.getsource(AgentCore.run)
    assert "self._last_repair_loop_guard_hits = 0" in src, (
        "AgentCore.run must reset self._last_repair_loop_guard_hits to 0 at "
        "entry — the line must live in run(), not __init__ alone, or a "
        "prior turn's count leaks into this turn's trace."
    )
