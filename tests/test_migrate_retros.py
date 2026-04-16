"""
O8 — ``rocky migrate-retros`` non-destructive migrator.

Default is ``--dry-run`` — no file state changes. In live mode, each
retrospective is re-scored against available trace payloads via
``ground_evidence_citations`` and flagged in frontmatter:

- grounded retros get ``grounded: true``
- ungrounded retros get ``unverified: true`` (or, with ``--quarantine``,
  move to ``.rocky/student/retrospectives/quarantine/``)

Originals are never deleted in-place.
"""
from __future__ import annotations

import json
from pathlib import Path

from rocky.commands.migrate_retros import cmd_migrate_retros


def _write_trace(traces_dir: Path, stem: str, payload: dict) -> Path:
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"trace_{stem}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_retro(
    retros_dir: Path,
    retro_id: str,
    *,
    evidence: list[str] | None = None,
    body: str = "",
) -> Path:
    retros_dir.mkdir(parents=True, exist_ok=True)
    import yaml

    meta = {
        "id": retro_id,
        "kind": "retrospective",
        "created_at": "2026-04-10T00:00:00Z",
        "task_signature": "research/general",
        "tags": ["test"],
    }
    if evidence:
        meta["evidence"] = evidence
    front = yaml.safe_dump(meta, sort_keys=False).rstrip() + "\n"
    path = retros_dir / f"{retro_id}.md"
    path.write_text(f"---\n{front}---\n{body or f'Body of {retro_id}.'}\n", encoding="utf-8")
    return path


def _setup_workspace(tmp_path: Path) -> None:
    traces = tmp_path / ".rocky" / "traces"
    _write_trace(
        traces,
        "one",
        {
            "route": {"task_signature": "research/general"},
            "tool_events": [
                {
                    "type": "tool_result",
                    "name": "fetch_url",
                    "stdout": "",
                    "content": "GitHub Trending shows microsoft/typescript as a trending repository.",
                }
            ],
        },
    )


# --------------------------------------------------------------------------
# 1. Dry-run: no file state changes.
# --------------------------------------------------------------------------


def test_dry_run_changes_no_files(tmp_path: Path, capsys) -> None:
    _setup_workspace(tmp_path)
    retros = tmp_path / ".rocky" / "student" / "retrospectives"
    _write_retro(retros, "retro-ungrounded", evidence=["Completely unrelated claim about widgets."])
    _write_retro(retros, "retro-grounded", evidence=["Microsoft typescript is trending on GitHub."])
    mtimes_before = {p.name: p.stat().st_mtime_ns for p in retros.glob("*.md")}

    code = cmd_migrate_retros(tmp_path, dry_run=True)
    assert code == 0

    mtimes_after = {p.name: p.stat().st_mtime_ns for p in retros.glob("*.md")}
    assert mtimes_after == mtimes_before, "dry-run must not modify files"


# --------------------------------------------------------------------------
# 2. Live: ungrounded retros get flagged, originals preserved.
# --------------------------------------------------------------------------


def test_live_flags_ungrounded_without_deleting(tmp_path: Path) -> None:
    _setup_workspace(tmp_path)
    retros = tmp_path / ".rocky" / "student" / "retrospectives"
    _write_retro(
        retros,
        "retro-widgets",
        evidence=["Widget Corp operates the blue gateway to amber systems."],
    )
    code = cmd_migrate_retros(tmp_path, dry_run=False)
    assert code == 0

    # File must still exist (never deleted in-place).
    path = retros / "retro-widgets.md"
    assert path.exists()

    text = path.read_text(encoding="utf-8")
    assert "unverified: true" in text.lower() or "unverified: True" in text


# --------------------------------------------------------------------------
# 3. Live: grounded retros get `grounded: true`, no unverified flag.
# --------------------------------------------------------------------------


def test_live_flags_grounded_retro(tmp_path: Path) -> None:
    _setup_workspace(tmp_path)
    retros = tmp_path / ".rocky" / "student" / "retrospectives"
    # Citation overlaps the trace: "microsoft typescript" + "trending" appear
    # in both the retro evidence and the fetch_url content.
    _write_retro(
        retros,
        "retro-typescript",
        evidence=[
            "Microsoft typescript is trending on GitHub this week.",
        ],
    )
    code = cmd_migrate_retros(tmp_path, dry_run=False)
    assert code == 0

    text = (retros / "retro-typescript.md").read_text(encoding="utf-8")
    assert "grounded: true" in text.lower() or "grounded: True" in text
    assert "unverified: true" not in text.lower()


# --------------------------------------------------------------------------
# 4. Quarantine moves ungrounded retros, originals are preserved in quarantine.
# --------------------------------------------------------------------------


def test_quarantine_moves_ungrounded(tmp_path: Path) -> None:
    _setup_workspace(tmp_path)
    retros = tmp_path / ".rocky" / "student" / "retrospectives"
    original = _write_retro(
        retros,
        "retro-widgets",
        evidence=["Widget Corp operates the blue gateway to amber systems."],
    )
    code = cmd_migrate_retros(tmp_path, dry_run=False, quarantine=True)
    assert code == 0

    quarantined = retros / "quarantine" / "retro-widgets.md"
    assert quarantined.exists(), "quarantine mode must preserve the file in quarantine dir"
    assert not original.exists(), "original file should be moved out of the main dir"


# --------------------------------------------------------------------------
# 5. Idempotence — running twice yields the same state.
# --------------------------------------------------------------------------


def test_live_mode_is_idempotent(tmp_path: Path) -> None:
    _setup_workspace(tmp_path)
    retros = tmp_path / ".rocky" / "student" / "retrospectives"
    _write_retro(
        retros,
        "retro-typescript",
        evidence=["Microsoft typescript is trending on GitHub."],
    )
    cmd_migrate_retros(tmp_path, dry_run=False)
    state_after_first = (retros / "retro-typescript.md").read_text(encoding="utf-8")
    cmd_migrate_retros(tmp_path, dry_run=False)
    state_after_second = (retros / "retro-typescript.md").read_text(encoding="utf-8")
    assert state_after_first == state_after_second


# --------------------------------------------------------------------------
# 6. CF-4: missing retros dir is a graceful exit.
# --------------------------------------------------------------------------


def test_missing_retros_dir_returns_nonzero(tmp_path: Path) -> None:
    # No workspace setup.
    code = cmd_migrate_retros(tmp_path, dry_run=True)
    assert code == 1
