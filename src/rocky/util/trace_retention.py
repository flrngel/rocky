"""Trace retention helper (O16).

Applies the retention policy configured at
:class:`rocky.config.models.TracingConfig` to a traces directory. Both limits
default to ``None`` (unlimited); eviction removes the oldest traces first when
either limit is exceeded.

Pure helper — no imports from rocky.core / rocky.app — so tests can exercise
it with a tmp directory and a synthetic list of file mtimes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _iter_trace_files(traces_dir: Path) -> list[Path]:
    """Return traces in the directory sorted by mtime ascending (oldest first)."""
    if not traces_dir.exists() or not traces_dir.is_dir():
        return []
    files = [p for p in traces_dir.glob("trace_*.json") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def evict_traces_if_needed(
    traces_dir: Path,
    *,
    max_age_days: int | None = None,
    max_trace_count: int | None = None,
) -> list[Path]:
    """Delete oldest traces until both constraints hold.

    - ``max_age_days``: if set, traces with mtime older than ``now -
      max_age_days`` are removed regardless of the count limit.
    - ``max_trace_count``: if set, additional oldest traces are removed until
      ``count <= max_trace_count``.

    Both ``None``: no-op (returns ``[]``).

    Returns the list of deleted file paths (empty if nothing was removed).
    """
    if max_age_days is None and max_trace_count is None:
        return []
    files = _iter_trace_files(traces_dir)
    if not files:
        return []

    deleted: list[Path] = []
    now_ts = datetime.now(timezone.utc).timestamp()

    # Age-based eviction.
    if max_age_days is not None and max_age_days >= 0:
        age_limit_s = max_age_days * 86400
        remaining: list[Path] = []
        for path in files:
            try:
                age = now_ts - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > age_limit_s:
                try:
                    path.unlink()
                    deleted.append(path)
                except OSError:
                    remaining.append(path)
            else:
                remaining.append(path)
        files = remaining

    # Count-based eviction on whatever is left.
    if max_trace_count is not None and max_trace_count >= 0:
        while len(files) > max_trace_count:
            oldest = files.pop(0)
            try:
                oldest.unlink()
                deleted.append(oldest)
            except FileNotFoundError:
                continue
            except OSError:
                break

    return deleted


def near_limit(
    traces_dir: Path,
    *,
    max_age_days: int | None = None,
    max_trace_count: int | None = None,
    warning_ratio: float = 0.9,
) -> dict[str, object]:
    """Return a summary dict of current usage vs configured limits.

    ``warning`` is True when either axis is at or above ``warning_ratio``
    (defaults 90%). Used by ``rocky stats`` to surface a proactive notice.
    """
    files = _iter_trace_files(traces_dir)
    count = len(files)
    summary: dict[str, object] = {
        "count": count,
        "max_trace_count": max_trace_count,
        "max_age_days": max_age_days,
        "warning": False,
    }
    if max_trace_count is not None and max_trace_count > 0:
        ratio = count / max_trace_count
        summary["count_ratio"] = ratio
        if ratio >= warning_ratio:
            summary["warning"] = True
    if max_age_days is not None and max_age_days > 0 and files:
        now_ts = datetime.now(timezone.utc).timestamp()
        oldest_age_days = (now_ts - files[0].stat().st_mtime) / 86400
        summary["oldest_age_days"] = oldest_age_days
        if oldest_age_days >= max_age_days * warning_ratio:
            summary["warning"] = True
    return summary


__all__ = ["evict_traces_if_needed", "near_limit"]
