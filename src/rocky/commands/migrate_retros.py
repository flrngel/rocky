"""``rocky migrate-retros`` — one-shot non-destructive migrator (O8).

Walks ``.rocky/student/retrospectives/*.md`` and annotates each retrospective's
frontmatter with ``grounded: true`` or ``unverified: true`` based on whether
evidence citations in the retro survive a re-check against linkable trace
payloads via :func:`rocky.util.evidence.ground_evidence_citations`.

Defaults:
- ``--dry-run`` is ON — no files are modified; a preview table is printed.
- ``--no-dry-run`` flips to live mode: frontmatter flags are written in-place;
  the retro body is never edited; files are never deleted in-place.
- ``--quarantine`` (live mode only) moves ungrounded retros into
  ``.rocky/student/retrospectives/quarantine/`` preserving mtime; originals are
  never deleted (they're moved with a copy-fallback if the filesystem does not
  support rename).

The migrator is idempotent: running it twice produces the same frontmatter as
running it once.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from rocky.util.evidence import ground_evidence_citations
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


def _load_trace_events(traces_dir: Path) -> list[dict[str, Any]]:
    if not traces_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for trace_file in sorted(traces_dir.glob("*.json")):
        try:
            payload = json.loads(read_text(trace_file))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        trace_events = payload.get("tool_events") or []
        if isinstance(trace_events, list):
            for event in trace_events:
                if isinstance(event, dict):
                    events.append(event)
    return events


def _classify_retro(
    retro_meta: dict[str, Any],
    retro_body: str,
    tool_events: list[dict[str, Any]],
) -> str:
    """Return ``"grounded"`` or ``"ungrounded"`` for the retro.

    A retro is grounded if *at least one* evidence citation from its
    frontmatter (if present) or a line-based extraction from the body
    survives :func:`ground_evidence_citations`. Retros with no citation
    material default to "ungrounded" so the operator can inspect them.
    """
    citations: list[str] = []
    raw = retro_meta.get("evidence") or retro_meta.get("citations") or []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                citations.append(item)
            elif isinstance(item, dict):
                for key in ("text", "citation", "summary", "value"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        citations.append(val)
                        break
    if not citations:
        # Fallback: treat each non-empty body line ≥ 24 chars as a candidate
        # citation. This deliberately overcollects to give the migrator a
        # chance to ground retros that did not serialize explicit evidence.
        citations = [ln.strip() for ln in retro_body.splitlines() if len(ln.strip()) >= 24]

    if not citations:
        return "ungrounded"

    kept = ground_evidence_citations(
        citations,
        tool_events,
        direction="retro",
        min_overlap=2,
    )
    return "grounded" if kept else "ungrounded"


def _quarantine_dir(retros_dir: Path) -> Path:
    return retros_dir / "quarantine"


def _apply_live(
    path: Path,
    meta: dict[str, Any],
    body: str,
    classification: str,
    *,
    quarantine: bool,
    quarantine_dir: Path,
) -> str:
    """Perform the live action. Returns a human-readable summary string."""
    if classification == "grounded":
        meta.pop("unverified", None)
        meta["grounded"] = True
        write_text(path, _write_frontmatter(meta, body))
        return f"flagged-grounded: {path.name}"

    # ungrounded
    if quarantine:
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        target = quarantine_dir / path.name
        if target.exists():
            target = quarantine_dir / f"{path.stem}.{int(path.stat().st_mtime)}.md"
        # Preserve the original by moving (never delete in place).
        shutil.move(str(path), str(target))
        return f"quarantined: {path.name} -> {target.relative_to(quarantine_dir.parent)}"
    meta.pop("grounded", None)
    meta["unverified"] = True
    write_text(path, _write_frontmatter(meta, body))
    return f"flagged-unverified: {path.name}"


def cmd_migrate_retros(
    cwd: Path,
    *,
    dry_run: bool = True,
    quarantine: bool = False,
    output_json: bool = False,
) -> int:
    """Run the migrator. Defaults to dry-run (safe)."""
    retros_dir = cwd / ".rocky" / "student" / "retrospectives"
    traces_dir = cwd / ".rocky" / "traces"
    if not retros_dir.exists():
        msg = f"no retrospectives directory at {retros_dir}"
        if output_json:
            print(json.dumps({"ok": False, "reason": msg}))
        else:
            print(msg)
        return 1

    events = _load_trace_events(traces_dir)
    quarantine_root = _quarantine_dir(retros_dir)

    results: list[dict[str, Any]] = []
    for path in sorted(retros_dir.glob("*.md")):
        # Skip files already inside the quarantine directory.
        if quarantine_root in path.parents:
            continue
        text = read_text(path)
        meta, body = _split_frontmatter(text)
        classification = _classify_retro(meta, body, events)

        entry = {
            "path": str(path),
            "id": str(meta.get("id") or path.stem),
            "classification": classification,
            "action": "dry-run (no change)",
        }

        if not dry_run:
            entry["action"] = _apply_live(
                path,
                meta,
                body,
                classification,
                quarantine=quarantine,
                quarantine_dir=quarantine_root,
            )
        results.append(entry)

    if output_json:
        print(json.dumps({"ok": True, "dry_run": dry_run, "results": results}, indent=2))
    else:
        title = "migrate-retros (dry-run)" if dry_run else "migrate-retros (live)"
        print(title)
        for row in results:
            print(f"  {row['classification']:<10}  {row['id']}  -> {row['action']}")
    return 0
