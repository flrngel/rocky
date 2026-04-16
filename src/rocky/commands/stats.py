"""rocky stats — aggregated view of routing decisions, tool calls, verification, and error modes.

O15 adds filter flags and a per-day breakdown:
- ``--since <YYYY-MM-DD>`` — include traces at or after this date.
- ``--last <N>`` — include only the N newest traces (by ``created_at``).
- ``--tool <name>`` — include only traces that used the named tool.
- ``--per-day`` — emit a row per calendar day in the filtered set.

O16 adds retention telemetry:
- When ``config.tracing.max_age_days`` / ``max_trace_count`` is set, a
  warning row is surfaced when the directory is at ≥90% of either limit.

O18 adds a loop-guard-hits aggregate surfaced in both table and JSON output.

CF-4: calling ``rocky_stats(cwd)`` with no flags produces identical output
to the pre-O15 / pre-O16 behavior, minus two always-present new keys in
the JSON output (``per_day`` = [], ``loop_guard_hits`` = 0, ``retention`` =
None) that existing consumers ignore.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _parse_created_at(trace: dict[str, Any], fallback_path: Path | None = None) -> datetime | None:
    """Return a UTC-naive or UTC-aware datetime for the trace's creation.

    Prefer ``trace["created_at"]`` / ``trace["timestamp"]`` when present;
    otherwise fall back to the file's mtime.
    """
    for key in ("created_at", "timestamp", "started_at"):
        raw = trace.get(key)
        if isinstance(raw, str) and raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
    if fallback_path is not None and fallback_path.exists():
        try:
            return datetime.fromtimestamp(fallback_path.stat().st_mtime)
        except OSError:
            return None
    return None


def _trace_uses_tool(trace: dict[str, Any], tool_filter: str | None) -> bool:
    if not tool_filter:
        return True
    events = trace.get("tool_events") or []
    if not isinstance(events, list):
        return False
    for event in events:
        if isinstance(event, dict):
            name = event.get("tool") or event.get("name") or event.get("tool_name")
            if name == tool_filter:
                return True
        elif isinstance(event, str) and event == tool_filter:
            return True
    return False


def rocky_stats(
    cwd: Path,
    output_json: bool = False,
    *,
    since: str | None = None,
    last: int | None = None,
    tool: str | None = None,
    per_day: bool = False,
) -> int:
    """Aggregate stats from .rocky/traces/*.json and optionally .rocky/ledger/*.jsonl.

    New (O15) keyword filters default to ``None``/``False``. Absent all
    flags, output is bit-identical with pre-O15 except for new additive
    fields (``per_day``, ``loop_guard_hits``, ``retention``).

    Returns exit code (0 on success).
    """
    rocky_dir = cwd / ".rocky"
    runs_dir = rocky_dir / "traces"
    ledger_dir = rocky_dir / "ledger"

    since_date: date | None = None
    if since:
        try:
            since_date = date.fromisoformat(since)
        except ValueError as exc:
            print(f"--since must be ISO date (YYYY-MM-DD): {exc}")
            return 2

    route_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    verification_status_counts: dict[str, int] = {}
    error_mode_counts: dict[str, int] = {}
    total_runs = 0
    loop_guard_hits_total = 0
    per_day_counts: dict[str, dict[str, Any]] = {}

    loaded: list[tuple[Path, dict[str, Any], datetime | None]] = []

    if runs_dir.exists():
        for trace_file in sorted(runs_dir.glob("*.json")):
            try:
                trace = json.loads(trace_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(trace, dict):
                continue
            ts = _parse_created_at(trace, fallback_path=trace_file)
            loaded.append((trace_file, trace, ts))

    # O15: --tool filter. Applied before --last / --since so the set of
    # "traces that used tool X" is what --last N reduces against.
    if tool:
        loaded = [(p, t, ts) for (p, t, ts) in loaded if _trace_uses_tool(t, tool)]

    # O15: --since filter — traces on or after the date.
    if since_date is not None:
        def _keep(entry):
            p, t, ts = entry
            if ts is None:
                return False
            return ts.date() >= since_date

        loaded = [e for e in loaded if _keep(e)]

    # O15: --last N — keep the N newest by timestamp (then by path).
    if last is not None and last >= 0:
        def _sort_key(entry):
            p, t, ts = entry
            return (ts or datetime.min, p.name)

        loaded.sort(key=_sort_key)
        loaded = loaded[-last:]

    # Aggregate over the final (filtered) set.
    for trace_file, trace, ts in loaded:
        total_runs += 1

        route = trace.get("route") or {}
        task_sig = route.get("task_signature") if isinstance(route, dict) else None
        if task_sig:
            route_counts[task_sig] = route_counts.get(task_sig, 0) + 1

        tool_events = trace.get("tool_events") or []
        if isinstance(tool_events, list):
            for event in tool_events:
                if isinstance(event, dict):
                    tool_name = event.get("tool") or event.get("name") or event.get("tool_name")
                    if tool_name:
                        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                elif isinstance(event, str):
                    tool_counts[event] = tool_counts.get(event, 0) + 1

        verification = trace.get("verification") or {}
        if isinstance(verification, dict):
            status = verification.get("status")
            if status:
                verification_status_counts[status] = verification_status_counts.get(status, 0) + 1
        elif isinstance(verification, str) and verification:
            verification_status_counts[verification] = verification_status_counts.get(verification, 0) + 1

        error_mode = trace.get("error_mode") or trace.get("error_modes")
        if error_mode:
            if isinstance(error_mode, str):
                error_mode_counts[error_mode] = error_mode_counts.get(error_mode, 0) + 1
            elif isinstance(error_mode, list):
                for em in error_mode:
                    if isinstance(em, str) and em:
                        error_mode_counts[em] = error_mode_counts.get(em, 0) + 1

        # O18: sum loop-guard hits surfaced by AgentCore.run().
        try:
            loop_guard_hits_total += int(trace.get("loop_guard_hits") or 0)
        except (TypeError, ValueError):
            pass

        # O15: per-day breakdown.
        if per_day and ts is not None:
            day_key = ts.date().isoformat()
            slot = per_day_counts.setdefault(day_key, {"date": day_key, "count": 0, "tools_used": {}})
            slot["count"] += 1
            if isinstance(tool_events, list):
                for event in tool_events:
                    nm = None
                    if isinstance(event, dict):
                        nm = event.get("tool") or event.get("name") or event.get("tool_name")
                    elif isinstance(event, str):
                        nm = event
                    if nm:
                        slot["tools_used"][nm] = int(slot["tools_used"].get(nm, 0)) + 1

    # Ledger (optional, skip silently if absent or unreadable).
    # Ledger aggregation is not currently filtered — the user-facing filters
    # target traces, not the learning ledger.
    if ledger_dir.exists():
        for jsonl_file in sorted(ledger_dir.glob("*.jsonl")):
            try:
                for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    error_mode = entry.get("error_mode") or entry.get("error_modes")
                    if error_mode:
                        if isinstance(error_mode, str):
                            error_mode_counts[error_mode] = error_mode_counts.get(error_mode, 0) + 1
                        elif isinstance(error_mode, list):
                            for em in error_mode:
                                if isinstance(em, str) and em:
                                    error_mode_counts[em] = error_mode_counts.get(em, 0) + 1
            except Exception:
                continue

    # O16: retention warning (if limits configured). Importing here keeps the
    # module light for callers who never configure retention.
    #
    # Review S2 fix — previously passed `cwd` as both `global_root` and
    # `workspace_root`, which meant the user's global config was never read.
    # Mirror `cli.py::_config_loader()`: discover the workspace (respecting
    # `.rocky/`) and resolve the real global root before loading config.
    retention_info: dict[str, Any] | None = None
    try:
        from rocky.config.loader import ConfigLoader
        from rocky.util.paths import discover_workspace, ensure_global_layout
        from rocky.util.trace_retention import near_limit as _retention_near

        global_root = ensure_global_layout(create_layout=False)
        workspace = discover_workspace(cwd)
        loader_cfg = ConfigLoader(global_root, workspace.root).load(create_defaults=False)
        tracing_cfg = getattr(loader_cfg, "tracing", None)
        if tracing_cfg is not None and (
            tracing_cfg.max_trace_count is not None or tracing_cfg.max_age_days is not None
        ):
            retention_info = _retention_near(
                runs_dir,
                max_age_days=tracing_cfg.max_age_days,
                max_trace_count=tracing_cfg.max_trace_count,
            )
    except Exception:
        retention_info = None

    per_day_list = [per_day_counts[k] for k in sorted(per_day_counts)] if per_day else []

    result: dict[str, Any] = {
        "route_counts": route_counts,
        "tool_counts": tool_counts,
        "verification_status_counts": verification_status_counts,
        "error_mode_counts": error_mode_counts,
        "total_runs": total_runs,
        "loop_guard_hits": loop_guard_hits_total,
        "per_day": per_day_list,
        "retention": retention_info,
    }

    if output_json:
        print(json.dumps(result, indent=2))
    else:
        _print_table(result)

    return 0


def _print_table(result: dict[str, Any]) -> None:
    total = result["total_runs"]
    print(f"Rocky stats  (total runs: {total})")
    print()

    sections: list[tuple[str, dict[str, int]]] = [
        ("Route counts", result["route_counts"]),
        ("Tool counts", result["tool_counts"]),
        ("Verification status", result["verification_status_counts"]),
        ("Error modes", result["error_mode_counts"]),
    ]

    for title, counts in sections:
        print(f"  {title}:")
        if counts:
            max_key = max(len(k) for k in counts)
            for key, val in sorted(counts.items(), key=lambda x: -x[1]):
                print(f"    {key:<{max_key}}  {val}")
        else:
            print("    (none)")
        print()

    # O18: loop-guard hits aggregate.
    print(f"  Loop-guard hits: {result.get('loop_guard_hits', 0)}")
    print()

    # O15: per-day breakdown.
    per_day = result.get("per_day") or []
    if per_day:
        print("  Per day:")
        for row in per_day:
            print(f"    {row['date']}  count={row['count']}  tools={row['tools_used']}")
        print()

    # O16: retention warning.
    retention = result.get("retention")
    if retention and retention.get("warning"):
        limit_count = retention.get("max_trace_count")
        count = retention.get("count")
        print(f"  [retention] traces dir at {count}/{limit_count} — approaching limit.")
        print()
