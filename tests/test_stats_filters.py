"""
O15 — ``rocky stats`` filter flags.

Each filter reduces the result set independently:

- ``--since <YYYY-MM-DD>`` keeps traces on/after that date.
- ``--last <N>`` keeps the N newest traces by ``created_at``.
- ``--tool <name>`` keeps only traces that used the named tool.
- ``--per-day`` emits one row per calendar day.

CF-4: calling ``rocky_stats(cwd)`` without flags produces bit-identical
totals for an unchanged fixture; new JSON keys (``per_day``,
``loop_guard_hits``, ``retention``) are additive.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from rocky.commands.stats import rocky_stats


def _write_trace(
    traces_dir: Path,
    stem: str,
    *,
    created_at: str,
    task_signature: str = "research/general",
    tools: list[str] | None = None,
    loop_guard_hits: int = 0,
) -> Path:
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"trace_{stem}.json"
    tool_events = [{"type": "tool_result", "name": t} for t in (tools or [])]
    payload = {
        "created_at": created_at,
        "route": {"task_signature": task_signature},
        "tool_events": tool_events,
        "verification": {"status": "pass"},
        "loop_guard_hits": loop_guard_hits,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _capture_stats(**kwargs) -> dict:
    """Invoke ``rocky_stats(output_json=True)`` and capture its JSON output."""
    saved = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        rocky_stats(output_json=True, **kwargs)
    finally:
        sys.stdout = saved
    return json.loads(buf.getvalue())


def _fixture_traces(tmp_path: Path) -> Path:
    traces = tmp_path / ".rocky" / "traces"
    # 6 traces spanning 3 days (day1 2, day2 2, day3 2).
    _write_trace(traces, "2026-04-10T09", created_at="2026-04-10T09:00:00", tools=["search_web"])
    _write_trace(traces, "2026-04-10T15", created_at="2026-04-10T15:00:00", tools=["fetch_url"])
    _write_trace(traces, "2026-04-11T09", created_at="2026-04-11T09:00:00", tools=["shell_exec", "search_web"])
    _write_trace(traces, "2026-04-11T15", created_at="2026-04-11T15:00:00", tools=["shell_exec"], loop_guard_hits=2)
    _write_trace(traces, "2026-04-12T09", created_at="2026-04-12T09:00:00", tools=["search_web"])
    _write_trace(traces, "2026-04-12T15", created_at="2026-04-12T15:00:00", tools=["fetch_url", "shell_exec"])
    return tmp_path


# --------------------------------------------------------------------------
# 1. No flags — bit-identical totals.
# --------------------------------------------------------------------------


def test_cf4_no_flags_totals_match_fixture(tmp_path: Path) -> None:
    _fixture_traces(tmp_path)
    result = _capture_stats(cwd=tmp_path)
    assert result["total_runs"] == 6
    assert result["loop_guard_hits"] == 2  # only one trace has loop_guard_hits>0


# --------------------------------------------------------------------------
# 2. --since filters by date.
# --------------------------------------------------------------------------


def test_since_filters_to_day2_and_later(tmp_path: Path) -> None:
    _fixture_traces(tmp_path)
    result = _capture_stats(cwd=tmp_path, since="2026-04-11")
    assert result["total_runs"] == 4


def test_since_rejects_invalid_date(tmp_path: Path) -> None:
    _fixture_traces(tmp_path)
    code = rocky_stats(cwd=tmp_path, output_json=True, since="not-a-date")
    assert code != 0


# --------------------------------------------------------------------------
# 3. --last keeps the N newest.
# --------------------------------------------------------------------------


def test_last_returns_n_newest_by_timestamp(tmp_path: Path) -> None:
    _fixture_traces(tmp_path)
    result = _capture_stats(cwd=tmp_path, last=3)
    assert result["total_runs"] == 3
    # The 3 newest are 2026-04-11T15 (has loop_guard_hits=2) + 2026-04-12 pair.
    # Anti-monkey: a "skip first N" implementation would return the 3 oldest
    # and thus the pair with loop_guard_hits=2 would be in the total, but the
    # totals would not match what --last should keep. Verify by tool presence:
    # shell_exec appears in 2026-04-11T15 and 2026-04-12T15, so tool_counts
    # for shell_exec should be >=1 under the 3-newest slice.
    assert result["tool_counts"].get("shell_exec", 0) >= 1
    # loop_guard_hits sums to 2 (only the 2026-04-11T15 trace has hits)
    assert result["loop_guard_hits"] == 2


# --------------------------------------------------------------------------
# 4. --tool keeps only traces that used the tool.
# --------------------------------------------------------------------------


def test_tool_filter_keeps_only_tool_using_traces(tmp_path: Path) -> None:
    _fixture_traces(tmp_path)
    result = _capture_stats(cwd=tmp_path, tool="shell_exec")
    assert result["total_runs"] == 3
    # Every included trace must have shell_exec in its tools.
    assert result["tool_counts"].get("shell_exec", 0) == 3


def test_tool_filter_zero_matches(tmp_path: Path) -> None:
    _fixture_traces(tmp_path)
    result = _capture_stats(cwd=tmp_path, tool="mysterious_tool")
    assert result["total_runs"] == 0


# --------------------------------------------------------------------------
# 5. --per-day emits one row per calendar day.
# --------------------------------------------------------------------------


def test_per_day_emits_one_row_per_calendar_day(tmp_path: Path) -> None:
    _fixture_traces(tmp_path)
    result = _capture_stats(cwd=tmp_path, per_day=True)
    rows = result["per_day"]
    assert len(rows) == 3
    assert [r["date"] for r in rows] == ["2026-04-10", "2026-04-11", "2026-04-12"]
    for row in rows:
        assert row["count"] == 2


def test_cf4_default_per_day_is_empty(tmp_path: Path) -> None:
    """Without --per-day, the per_day list is empty (not the full breakdown)."""
    _fixture_traces(tmp_path)
    result = _capture_stats(cwd=tmp_path)
    assert result["per_day"] == []
