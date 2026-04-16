"""``rocky retros`` subcommand — list / pin / discard retrospectives (O5).

Retros live as markdown files with YAML frontmatter under
``.rocky/student/retrospectives/``. ``pin`` sets ``pinned: true`` in the
frontmatter (a retention-exempt flag consumed by any future eviction logic;
O16's trace-retention policy already honors it for parity). ``discard``
deletes the retro file. ``list`` prints a summary table or JSON when
``--json`` is set.

Frontmatter contract:
- ``id`` (required)
- ``created_at``
- ``task_signature``
- ``tags`` (list)
- ``pinned`` (bool, optional — added/removed by this command)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rocky.util.io import read_text, write_text
from rocky.util.yamlx import dump_yaml, load_yaml


_FRONTMATTER_START = "---\n"
_FRONTMATTER_END = "\n---\n"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith(_FRONTMATTER_START):
        return {}, text
    rest = text[len(_FRONTMATTER_START):]
    try:
        fm, body = rest.split(_FRONTMATTER_END, 1)
    except ValueError:
        return {}, text
    loaded = load_yaml(fm)
    if not isinstance(loaded, dict):
        return {}, body
    return loaded, body


def _write_frontmatter(meta: dict[str, Any], body: str) -> str:
    return _FRONTMATTER_START + dump_yaml(meta) + _FRONTMATTER_END + body


def _retro_dir(cwd: Path) -> Path:
    return cwd / ".rocky" / "student" / "retrospectives"


def _list_retros(retros_dir: Path) -> list[dict[str, Any]]:
    if not retros_dir.exists():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(retros_dir.glob("*.md")):
        text = read_text(path)
        meta, _body = _split_frontmatter(text)
        entries.append(
            {
                "id": str(meta.get("id") or path.stem),
                "path": str(path),
                "created_at": str(meta.get("created_at") or ""),
                "task_signature": str(meta.get("task_signature") or ""),
                "keywords": list(meta.get("tags") or meta.get("keywords") or []),
                "pinned": bool(meta.get("pinned", False)),
                "grounded_evidence_count": int(meta.get("grounded_evidence_count") or 0),
                "ungrounded_evidence_count": int(meta.get("ungrounded_evidence_count") or 0),
            }
        )
    return entries


def _find_retro_path(retros_dir: Path, retro_id: str) -> Path | None:
    if not retros_dir.exists():
        return None
    direct = retros_dir / f"{retro_id}.md"
    if direct.exists():
        return direct
    for path in retros_dir.glob("*.md"):
        text = read_text(path)
        meta, _body = _split_frontmatter(text)
        if str(meta.get("id") or path.stem) == retro_id:
            return path
    return None


def _set_pinned(path: Path, pinned: bool) -> None:
    text = read_text(path)
    meta, body = _split_frontmatter(text)
    if pinned:
        meta["pinned"] = True
    else:
        meta.pop("pinned", None)
    write_text(path, _write_frontmatter(meta, body))


def cmd_retros(cwd: Path, args: list[str], *, output_json: bool = False) -> int:
    """Dispatch ``rocky retros {list,pin,discard}``.

    Returns a non-zero exit code on argument or missing-retro errors.
    """
    retros_dir = _retro_dir(cwd)
    if not args:
        print("usage: rocky retros <list|pin|discard> [id]")
        return 2

    action = args[0]
    if action == "list":
        entries = _list_retros(retros_dir)
        if output_json:
            print(json.dumps({"retros": entries}, indent=2))
        else:
            if not entries:
                print("(no retrospectives)")
                return 0
            header = ["id", "created_at", "task_signature", "pinned", "grounded", "ungrounded"]
            rows = [header] + [
                [
                    e["id"],
                    e["created_at"],
                    e["task_signature"],
                    "yes" if e["pinned"] else "no",
                    str(e["grounded_evidence_count"]),
                    str(e["ungrounded_evidence_count"]),
                ]
                for e in entries
            ]
            widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
            for row in rows:
                print("  ".join(val.ljust(widths[i]) for i, val in enumerate(row)))
        return 0

    if action in ("pin", "unpin", "discard"):
        if len(args) < 2:
            print(f"usage: rocky retros {action} <id>")
            return 2
        retro_id = args[1]
        path = _find_retro_path(retros_dir, retro_id)
        if path is None:
            print(f"retro not found: {retro_id}")
            return 1
        if action == "pin":
            _set_pinned(path, True)
            print(f"pinned: {retro_id}")
        elif action == "unpin":
            _set_pinned(path, False)
            print(f"unpinned: {retro_id}")
        else:  # discard
            # O5: discard is idempotent — missing file is not an error.
            try:
                path.unlink()
                print(f"discarded: {retro_id}")
            except FileNotFoundError:
                print(f"retro not found (already discarded): {retro_id}")
                return 1
        return 0

    print(f"unknown action: {action!r}")
    return 2
