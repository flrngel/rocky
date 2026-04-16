"""rocky stats — aggregated view of routing decisions, tool calls, verification, and error modes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def rocky_stats(cwd: Path, output_json: bool = False) -> int:
    """Aggregate stats from .rocky/traces/*.json and optionally .rocky/ledger/*.jsonl.

    Returns exit code (0 on success).
    """
    rocky_dir = cwd / ".rocky"
    runs_dir = rocky_dir / "traces"
    ledger_dir = rocky_dir / "ledger"

    route_counts: dict[str, int] = {}
    tool_counts: dict[str, int] = {}
    verification_status_counts: dict[str, int] = {}
    error_mode_counts: dict[str, int] = {}
    total_runs = 0

    if runs_dir.exists():
        for trace_file in sorted(runs_dir.glob("*.json")):
            try:
                raw = trace_file.read_text(encoding="utf-8")
                trace = json.loads(raw)
            except Exception:
                # Skip corrupt or unreadable files
                continue

            # Validate it looks like a real trace (not just any JSON)
            if not isinstance(trace, dict):
                continue

            total_runs += 1

            # Route
            route = trace.get("route") or {}
            task_sig = route.get("task_signature") if isinstance(route, dict) else None
            if task_sig:
                route_counts[task_sig] = route_counts.get(task_sig, 0) + 1

            # Tool events
            tool_events = trace.get("tool_events") or []
            if isinstance(tool_events, list):
                for event in tool_events:
                    if isinstance(event, dict):
                        tool_name = event.get("tool") or event.get("name") or event.get("tool_name")
                        if tool_name:
                            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                    elif isinstance(event, str):
                        tool_counts[event] = tool_counts.get(event, 0) + 1

            # Verification status
            verification = trace.get("verification") or {}
            if isinstance(verification, dict):
                status = verification.get("status")
                if status:
                    verification_status_counts[status] = verification_status_counts.get(status, 0) + 1
            elif isinstance(verification, str) and verification:
                verification_status_counts[verification] = verification_status_counts.get(verification, 0) + 1

            # Error modes (optional field)
            error_mode = trace.get("error_mode") or trace.get("error_modes")
            if error_mode:
                if isinstance(error_mode, str):
                    error_mode_counts[error_mode] = error_mode_counts.get(error_mode, 0) + 1
                elif isinstance(error_mode, list):
                    for em in error_mode:
                        if isinstance(em, str) and em:
                            error_mode_counts[em] = error_mode_counts.get(em, 0) + 1

    # Ledger (optional, skip silently if absent or unreadable)
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

    result: dict[str, Any] = {
        "route_counts": route_counts,
        "tool_counts": tool_counts,
        "verification_status_counts": verification_status_counts,
        "error_mode_counts": error_mode_counts,
        "total_runs": total_runs,
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
