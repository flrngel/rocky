"""
O5 — ``rocky retros {list, pin, discard}`` CLI.

Retrospective files live under ``.rocky/student/retrospectives/*.md`` with
YAML frontmatter. The subcommand supports:

- ``list`` — schema-valid listing (id, created_at, task_signature, keywords,
  grounded/ungrounded evidence counts, pinned).
- ``pin <id>`` — set ``pinned: true`` in the frontmatter; round-trips.
- ``discard <id>`` — delete the retro file.

Pin semantics interact with any eviction logic (O16 retention knobs): pinned
retros are exempt. Since O16's initial retention policy targets the
``.rocky/traces/`` directory, pin-exemption is carried via the frontmatter
flag so any future retro-directory eviction logic will find it.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from rocky.commands.retros import cmd_retros


def _write_retro(
    retros_dir: Path,
    retro_id: str,
    *,
    task_signature: str = "research/general",
    tags: list[str] | None = None,
    grounded: int = 0,
    ungrounded: int = 0,
    pinned: bool | None = None,
) -> Path:
    retros_dir.mkdir(parents=True, exist_ok=True)
    meta_lines = [
        "---",
        f"id: {retro_id}",
        "created_at: '2026-04-10T00:00:00Z'",
        f"task_signature: {task_signature}",
        "tags:",
    ]
    for tag in tags or ["test"]:
        meta_lines.append(f"  - {tag}")
    meta_lines.append(f"grounded_evidence_count: {grounded}")
    meta_lines.append(f"ungrounded_evidence_count: {ungrounded}")
    if pinned is not None:
        meta_lines.append(f"pinned: {'true' if pinned else 'false'}")
    meta_lines.append("---")
    meta = "\n".join(meta_lines) + "\n"
    body = f"# {retro_id}\n\nSynthetic retro body.\n"
    path = retros_dir / f"{retro_id}.md"
    path.write_text(meta + body, encoding="utf-8")
    return path


def _capture_json(cwd: Path, tail: list[str]) -> dict:
    saved = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        cmd_retros(cwd, tail, output_json=True)
    finally:
        sys.stdout = saved
    return json.loads(buf.getvalue())


# --------------------------------------------------------------------------
# 1. list shows schema-valid entries.
# --------------------------------------------------------------------------


def test_list_reports_all_retros_with_schema(tmp_path: Path) -> None:
    retros_dir = tmp_path / ".rocky" / "student" / "retrospectives"
    _write_retro(retros_dir, "retro-alpha", grounded=2, ungrounded=1)
    _write_retro(retros_dir, "retro-beta", task_signature="site/understanding/general")
    _write_retro(retros_dir, "retro-gamma")

    result = _capture_json(tmp_path, ["list"])
    ids = {r["id"] for r in result["retros"]}
    assert ids == {"retro-alpha", "retro-beta", "retro-gamma"}
    for entry in result["retros"]:
        assert set(entry.keys()) >= {
            "id",
            "created_at",
            "task_signature",
            "keywords",
            "pinned",
            "grounded_evidence_count",
            "ungrounded_evidence_count",
        }


# --------------------------------------------------------------------------
# 2. pin round-trips.
# --------------------------------------------------------------------------


def test_pin_sets_frontmatter_flag(tmp_path: Path) -> None:
    retros_dir = tmp_path / ".rocky" / "student" / "retrospectives"
    path = _write_retro(retros_dir, "retro-to-pin")
    code = cmd_retros(tmp_path, ["pin", "retro-to-pin"])
    assert code == 0
    result = _capture_json(tmp_path, ["list"])
    matched = [r for r in result["retros"] if r["id"] == "retro-to-pin"]
    assert matched and matched[0]["pinned"] is True


def test_unpin_clears_frontmatter_flag(tmp_path: Path) -> None:
    retros_dir = tmp_path / ".rocky" / "student" / "retrospectives"
    _write_retro(retros_dir, "retro-to-unpin", pinned=True)
    result_before = _capture_json(tmp_path, ["list"])
    assert result_before["retros"][0]["pinned"] is True

    code = cmd_retros(tmp_path, ["unpin", "retro-to-unpin"])
    assert code == 0
    result_after = _capture_json(tmp_path, ["list"])
    assert result_after["retros"][0]["pinned"] is False


# --------------------------------------------------------------------------
# 3. discard removes the file.
# --------------------------------------------------------------------------


def test_discard_removes_file(tmp_path: Path) -> None:
    retros_dir = tmp_path / ".rocky" / "student" / "retrospectives"
    _write_retro(retros_dir, "retro-to-discard")
    path = retros_dir / "retro-to-discard.md"
    assert path.exists()

    code = cmd_retros(tmp_path, ["discard", "retro-to-discard"])
    assert code == 0
    assert not path.exists()


def test_discard_missing_retro_exits_nonzero(tmp_path: Path) -> None:
    code = cmd_retros(tmp_path, ["discard", "never-created"])
    assert code == 1


# --------------------------------------------------------------------------
# 4. Pinned retros survive a simulated eviction pass.
#    Exercises the same frontmatter contract any future eviction logic
#    would consume.
# --------------------------------------------------------------------------


def test_pinned_retro_survives_simulated_eviction(tmp_path: Path) -> None:
    retros_dir = tmp_path / ".rocky" / "student" / "retrospectives"
    _write_retro(retros_dir, "retro-pinned", pinned=True)
    _write_retro(retros_dir, "retro-ephemeral")

    # Pin via the subcommand (double-covers the round-trip above).
    cmd_retros(tmp_path, ["pin", "retro-pinned"])

    # Simulated eviction: delete every *.md that does not carry pinned: true.
    from rocky.util.io import read_text
    import re

    for path in list(retros_dir.glob("*.md")):
        text = read_text(path)
        if not re.search(r"^pinned:\s*true\b", text, re.M):
            path.unlink()

    remaining = {p.stem for p in retros_dir.glob("*.md")}
    assert remaining == {"retro-pinned"}


# --------------------------------------------------------------------------
# 5. CF-4: invoking with no args prints usage and returns 2 (non-zero).
# --------------------------------------------------------------------------


def test_no_args_prints_usage(tmp_path: Path, capsys) -> None:
    code = cmd_retros(tmp_path, [])
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()
    assert code == 2
