Status: DONE  # xlfg artifact marker — O16 rocky stats subcommand
import json
import subprocess
from pathlib import Path

import pytest

from rocky.commands.stats import rocky_stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace(
    tmp_path: Path,
    name: str,
    task_signature: str,
    tool_events: list,
    verification_status: str,
    error_mode: str | None = None,
) -> Path:
    """Write a minimal run trace JSON into tmp_path/.rocky/traces/ (the real trace path)."""
    runs_dir = tmp_path / ".rocky" / "traces"
    runs_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {
        "route": {"task_signature": task_signature},
        "tool_events": tool_events,
        "verification": {"status": verification_status},
    }
    if error_mode:
        trace["error_mode"] = error_mode
    p = runs_dir / name
    p.write_text(json.dumps(trace), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. Happy path (in-process)
# ---------------------------------------------------------------------------

def test_happy_path_single_trace(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _make_trace(
        tmp_path,
        "run-fake-001.json",
        task_signature="research/live_compare/general",
        tool_events=[{"tool": "search_web"}, {"tool": "search_web"}],
        verification_status="pass",
    )

    rc = rocky_stats(cwd=tmp_path, output_json=True)
    assert rc == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["route_counts"]["research/live_compare/general"] == 1
    assert data["tool_counts"]["search_web"] >= 1
    assert data["verification_status_counts"]["pass"] == 1
    assert data["total_runs"] == 1


# ---------------------------------------------------------------------------
# 2. Missing .rocky/ (edge case)
# ---------------------------------------------------------------------------

def test_missing_rocky_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # No .rocky/ directory at all
    rc = rocky_stats(cwd=tmp_path, output_json=True)
    assert rc == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["total_runs"] == 0
    assert data["route_counts"] == {}
    assert data["tool_counts"] == {}
    assert data["verification_status_counts"] == {}
    assert data["error_mode_counts"] == {}


# ---------------------------------------------------------------------------
# 3. Multiple runs with mixed routes and statuses
# ---------------------------------------------------------------------------

def test_multiple_runs(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _make_trace(tmp_path, "run-001.json", "research/live_compare/general",
                [{"tool": "search_web"}], "pass")
    _make_trace(tmp_path, "run-002.json", "repo/shell_execution",
                [{"tool": "run_shell_command"}, {"tool": "run_shell_command"}], "pass")
    _make_trace(tmp_path, "run-003.json", "research/live_compare/general",
                [{"tool": "search_web"}, {"tool": "read_file"}], "needs_review",
                error_mode="tool_not_exposed")

    rc = rocky_stats(cwd=tmp_path, output_json=True)
    assert rc == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["total_runs"] == 3
    assert data["route_counts"]["research/live_compare/general"] == 2
    assert data["route_counts"]["repo/shell_execution"] == 1
    assert data["tool_counts"]["search_web"] == 2
    assert data["tool_counts"]["run_shell_command"] == 2
    assert data["tool_counts"]["read_file"] == 1
    assert data["verification_status_counts"]["pass"] == 2
    assert data["verification_status_counts"]["needs_review"] == 1
    assert data["error_mode_counts"]["tool_not_exposed"] == 1


# ---------------------------------------------------------------------------
# 4. Corrupt JSON is skipped gracefully
# ---------------------------------------------------------------------------

def test_corrupt_json_skipped(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    # One valid trace
    _make_trace(tmp_path, "run-valid.json", "repo/shell_execution",
                [{"tool": "run_shell_command"}], "pass")

    # One corrupt trace (unterminated JSON)
    runs_dir = tmp_path / ".rocky" / "traces"
    corrupt = runs_dir / "run-corrupt.json"
    corrupt.write_text('{"bogus": "json"', encoding="utf-8")

    rc = rocky_stats(cwd=tmp_path, output_json=True)
    assert rc == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    # Only the valid trace is counted
    assert data["total_runs"] == 1
    assert data["route_counts"].get("repo/shell_execution") == 1


# ---------------------------------------------------------------------------
# 5. CLI subprocess smoke test
# ---------------------------------------------------------------------------

_ROCKY_BIN = Path(__file__).parent.parent / ".venv" / "bin" / "rocky"


@pytest.mark.skipif(not _ROCKY_BIN.exists(), reason="rocky CLI not installed in venv")
def test_cli_stats_subprocess(tmp_path: Path) -> None:
    _make_trace(
        tmp_path,
        "run-smoke.json",
        task_signature="repo/shell_execution",
        tool_events=[{"tool": "run_shell_command"}],
        verification_status="pass",
    )

    proc = subprocess.run(
        [str(_ROCKY_BIN), "stats", "--cwd", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    data = json.loads(proc.stdout)
    assert "route_counts" in data
    assert data["total_runs"] >= 1


# ---------------------------------------------------------------------------
# 6. Real trace filename pattern — guard against directory/format drift
# ---------------------------------------------------------------------------

def test_real_trace_filename_pattern(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """stats must count traces written with the real agent.py filename pattern
    (`trace_<stamp>.json` in `.rocky/traces/`). This guards against silent
    directory drift like the previous `runs/` vs `traces/` mismatch."""
    traces_dir = tmp_path / ".rocky" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / "trace_20260416_120000.json").write_text(
        json.dumps({
            "route": {"task_signature": "research/live_compare/general"},
            "tool_events": [{"tool": "search_web"}],
            "verification": {"status": "pass"},
        }),
        encoding="utf-8",
    )

    rc = rocky_stats(cwd=tmp_path, output_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total_runs"] == 1, (
        "stats must read traces from .rocky/traces/ using the real filename pattern; "
        f"got {data}"
    )
    assert data["route_counts"]["research/live_compare/general"] == 1
