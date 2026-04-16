"""
O16 — Trace retention policy.

``config.tracing.max_age_days`` / ``max_trace_count`` bound the size of
``.rocky/traces/``. Defaults are ``None`` (unlimited) so existing callers
see no eviction — CF-4. When a limit is set, oldest traces are removed
first until both constraints hold.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rocky.util.trace_retention import evict_traces_if_needed, near_limit


def _write_trace(
    traces_dir: Path,
    stamp: str,
    payload: dict | None = None,
    *,
    mtime_offset_s: float = 0.0,
) -> Path:
    """Write a synthetic trace file with a controllable mtime."""
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"trace_{stamp}.json"
    path.write_text(json.dumps(payload or {"stamp": stamp}), encoding="utf-8")
    if mtime_offset_s:
        ts = time.time() + mtime_offset_s
        import os

        os.utime(path, (ts, ts))
    return path


# --------------------------------------------------------------------------
# 1. Count-based eviction — oldest removed first.
# --------------------------------------------------------------------------


def test_max_trace_count_evicts_oldest_first(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    paths = []
    for i in range(5):
        # Space the mtimes out so ordering is unambiguous.
        paths.append(_write_trace(traces, f"{i:04d}", mtime_offset_s=i * -100))
    # Oldest -> newest: paths[4], paths[3], paths[2], paths[1], paths[0]
    # (index 4 has mtime = now - 400, most negative offset = oldest)
    deleted = evict_traces_if_needed(traces, max_trace_count=2)
    remaining = sorted(traces.glob("trace_*.json"))
    assert len(remaining) == 2
    assert len(deleted) == 3
    # The two newest (largest mtime) must remain. Here paths[0] has offset=0
    # (newest) and paths[1] has offset=-100. Those survive.
    survived_names = {p.name for p in remaining}
    assert paths[0].name in survived_names
    assert paths[1].name in survived_names


def test_default_no_limit_evicts_nothing(tmp_path: Path) -> None:
    """CF-4: when both limits are None, nothing is evicted even with 100 files."""
    traces = tmp_path / "traces"
    for i in range(10):
        _write_trace(traces, f"{i:04d}")
    deleted = evict_traces_if_needed(traces, max_age_days=None, max_trace_count=None)
    assert deleted == []
    assert len(list(traces.glob("trace_*.json"))) == 10


# --------------------------------------------------------------------------
# 2. Age-based eviction.
# --------------------------------------------------------------------------


def test_max_age_days_evicts_stale_traces(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    fresh = _write_trace(traces, "fresh", mtime_offset_s=0)
    _write_trace(traces, "stale", mtime_offset_s=-(86400 * 10))  # 10 days old

    deleted = evict_traces_if_needed(traces, max_age_days=5)
    remaining = list(traces.glob("trace_*.json"))
    assert fresh in remaining
    assert len(deleted) == 1
    assert "stale" in deleted[0].name


def test_both_limits_apply(tmp_path: Path) -> None:
    """When age + count are both set, both constraints must hold afterward."""
    traces = tmp_path / "traces"
    # 3 fresh traces + 2 stale traces. max_age_days=5 wipes stale; then
    # max_trace_count=1 wipes 2 of the 3 remaining fresh ones.
    for i in range(3):
        _write_trace(traces, f"fresh_{i}", mtime_offset_s=-(i * 10))
    _write_trace(traces, "stale_1", mtime_offset_s=-(86400 * 20))
    _write_trace(traces, "stale_2", mtime_offset_s=-(86400 * 15))

    evict_traces_if_needed(traces, max_age_days=5, max_trace_count=1)
    remaining = sorted(traces.glob("trace_*.json"))
    assert len(remaining) == 1


# --------------------------------------------------------------------------
# 3. near_limit helper surfaces warnings.
# --------------------------------------------------------------------------


def test_near_limit_warns_at_or_above_ratio(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    for i in range(9):
        _write_trace(traces, f"{i:04d}")
    info = near_limit(traces, max_trace_count=10, warning_ratio=0.9)
    assert info["count"] == 9
    assert info["warning"] is True


def test_near_limit_no_warning_when_below_ratio(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    for i in range(3):
        _write_trace(traces, f"{i:04d}")
    info = near_limit(traces, max_trace_count=10, warning_ratio=0.9)
    assert info["count"] == 3
    assert info["warning"] is False


def test_empty_directory_is_safe(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    deleted = evict_traces_if_needed(traces, max_trace_count=5)
    assert deleted == []
    info = near_limit(traces, max_trace_count=5)
    assert info["count"] == 0
    assert info["warning"] is False


# --------------------------------------------------------------------------
# 4. CF-4 integration guard — default AppConfig does not trigger eviction.
# --------------------------------------------------------------------------


def test_default_tracing_config_is_unlimited() -> None:
    from rocky.config.models import AppConfig

    cfg = AppConfig.default()
    assert cfg.tracing.max_age_days is None
    assert cfg.tracing.max_trace_count is None
