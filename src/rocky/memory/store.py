from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from rocky.util.io import read_text, write_text
from rocky.util.text import tokenize_keywords
from rocky.util.time import utc_iso


AUTO_LIMIT = 3
AUTO_KINDS = {
    "goal",
    "constraint",
    "preference",
    "decision",
    "important_path",
    "workflow_rule",
}
AUTO_KIND_PRIORITY = {
    "goal": 6,
    "constraint": 5,
    "preference": 4,
    "decision": 4,
    "important_path": 3,
    "workflow_rule": 2,
    "project_brief": 10,
    "manual": 1,
}
PATH_RE = re.compile(r"(?:^|[\s`'\"(])((?:\./|\.rocky/|src/|tests/|docs/|README\.md|[A-Za-z0-9._-]+/)[^\s`'\"()]+)")
SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")[:64] or "memory"


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _fingerprint(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}:{_clean_text(text).lower()}".encode("utf-8")).hexdigest()


def _path_candidates(text: str) -> list[str]:
    values: list[str] = []
    for match in PATH_RE.findall(text):
        candidate = match.strip("`'\"()[]{}.,:;")
        if not candidate or candidate.startswith(".rocky/") or candidate.startswith(".git/"):
            continue
        if candidate not in values:
            values.append(candidate)
    return values


def _title_from_text(kind: str, text: str) -> str:
    if kind == "important_path":
        paths = _path_candidates(text)
        if paths:
            return paths[0]
    words = [
        word
        for word in re.findall(r"[a-zA-Z0-9_:+./-]+", text.lower())
        if len(word) > 2 and word not in AUTO_KINDS
    ]
    if not words:
        return kind.replace("_", " ")
    return " ".join(words[:8])


@dataclass(slots=True)
class MemoryNote:
    id: str
    name: str
    title: str
    scope: str
    origin: str
    kind: str
    text: str
    created_at: str
    updated_at: str
    source_task_signature: str
    evidence_excerpt: str
    fingerprint: str
    path: Path
    writable: bool

    def as_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "scope": self.scope,
            "origin": self.origin,
            "kind": self.kind,
            "writable": self.writable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "path": str(self.path),
        }

    def keyword_text(self) -> str:
        return " ".join(part for part in (self.title, self.text, self.kind, self.name) if part)


@dataclass(slots=True)
class MemoryCandidate:
    kind: str
    title: str
    text: str
    source_task_signature: str
    evidence_excerpt: str

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.kind, self.text)


class MemoryStore:
    def __init__(self, project_dir: Path, global_dir: Path, *, create_layout: bool = True) -> None:
        self.project_dir = project_dir
        self.global_dir = global_dir
        self.project_auto_dir = project_dir / "auto"
        self.project_brief_path = project_dir / "project_brief.md"
        self.global_manual_dir = global_dir / "global"
        if create_layout:
            self.ensure_layout()

    def ensure_layout(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.project_auto_dir.mkdir(parents=True, exist_ok=True)
        self.global_dir.mkdir(parents=True, exist_ok=True)
        self.global_manual_dir.mkdir(parents=True, exist_ok=True)

    def _brief_note(self) -> MemoryNote | None:
        if not self.project_brief_path.exists():
            return None
        text = _clean_text(read_text(self.project_brief_path))
        if not text:
            return None
        return MemoryNote(
            id="project-brief",
            name="project-brief",
            title="Project brief",
            scope="project_auto",
            origin="auto",
            kind="project_brief",
            text=read_text(self.project_brief_path),
            created_at="",
            updated_at="",
            source_task_signature="",
            evidence_excerpt="Automatically synthesized project brief",
            fingerprint=_fingerprint("project_brief", text),
            path=self.project_brief_path,
            writable=False,
        )

    def _note_from_payload(
        self,
        path: Path,
        payload: dict[str, Any],
        *,
        scope: str,
        origin: str,
        writable: bool,
    ) -> MemoryNote:
        text = str(payload.get("text", ""))
        title = str(payload.get("title") or path.stem.replace("-", " "))
        kind = str(payload.get("kind") or ("manual" if scope == "global_manual" else "decision"))
        name = str(payload.get("name") or path.stem)
        return MemoryNote(
            id=str(payload.get("id") or name),
            name=name,
            title=title,
            scope=scope,
            origin=str(payload.get("origin") or origin),
            kind=kind,
            text=text,
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            source_task_signature=str(payload.get("source_task_signature") or ""),
            evidence_excerpt=str(payload.get("evidence_excerpt") or ""),
            fingerprint=str(payload.get("fingerprint") or _fingerprint(kind, text)),
            path=path,
            writable=writable,
        )

    def _load_json_note(self, path: Path, *, scope: str, origin: str, writable: bool) -> MemoryNote | None:
        try:
            payload = json.loads(read_text(path))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return self._note_from_payload(path, payload, scope=scope, origin=origin, writable=writable)

    def _load_global_markdown_note(self, path: Path) -> MemoryNote:
        text = read_text(path)
        name = path.stem
        return MemoryNote(
            id=name,
            name=name,
            title=name.replace("-", " "),
            scope="global_manual",
            origin="user",
            kind="manual",
            text=text,
            created_at="",
            updated_at="",
            source_task_signature="",
            evidence_excerpt="",
            fingerprint=_fingerprint("manual", text),
            path=path,
            writable=True,
        )

    def project_brief_note(self) -> MemoryNote | None:
        return self._brief_note()

    def load_project_auto_notes(self) -> list[MemoryNote]:
        notes: list[MemoryNote] = []
        if not self.project_auto_dir.exists():
            return notes
        for path in sorted(self.project_auto_dir.glob("*.json")):
            note = self._load_json_note(path, scope="project_auto", origin="auto", writable=False)
            if note is not None:
                notes.append(note)
        notes.sort(key=lambda item: (item.updated_at, item.name), reverse=True)
        return notes

    def load_global_manual_notes(self) -> list[MemoryNote]:
        notes: list[MemoryNote] = []
        if not self.global_manual_dir.exists():
            return notes
        for path in sorted(self.global_manual_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".json":
                note = self._load_json_note(path, scope="global_manual", origin="user", writable=True)
                if note is not None:
                    notes.append(note)
                continue
            if path.suffix.lower() in {".md", ".txt", ".yaml", ".yml"}:
                try:
                    notes.append(self._load_global_markdown_note(path))
                except Exception:
                    continue
        notes.sort(key=lambda item: (item.updated_at, item.name), reverse=True)
        return notes

    def load_all(self) -> list[MemoryNote]:
        notes: list[MemoryNote] = []
        brief = self.project_brief_note()
        if brief is not None:
            notes.append(brief)
        notes.extend(self.load_project_auto_notes())
        notes.extend(self.load_global_manual_notes())
        return notes

    def inventory(self) -> list[dict[str, Any]]:
        return [note.as_record() for note in self.load_all()]

    def get_note(self, scope: str, name: str) -> MemoryNote | None:
        for note in self.load_all():
            if note.scope == scope and note.name == name:
                return note
        return None

    def _global_manual_path(self, name: str) -> Path:
        return self.global_manual_dir / f"{_slug(name)}.json"

    def add_global_manual(self, name: str, text: str) -> dict[str, Any]:
        slug = _slug(name)
        path = self._global_manual_path(slug)
        if path.exists():
            return {"ok": False, "reason": f"global memory already exists: {slug}"}
        return self.set_global_manual(slug, text, title=name, create_only=True)

    def set_global_manual(
        self,
        name: str,
        text: str,
        *,
        title: str | None = None,
        create_only: bool = False,
    ) -> dict[str, Any]:
        slug = _slug(name)
        path = self._global_manual_path(slug)
        existing = self._load_json_note(path, scope="global_manual", origin="user", writable=True) if path.exists() else None
        if create_only and existing is not None:
            return {"ok": False, "reason": f"global memory already exists: {slug}"}
        created_at = existing.created_at if existing is not None else utc_iso()
        updated_at = utc_iso()
        payload = {
            "id": slug,
            "name": slug,
            "title": title or (existing.title if existing is not None else name),
            "scope": "global_manual",
            "origin": "user",
            "kind": "manual",
            "text": text.strip(),
            "created_at": created_at,
            "updated_at": updated_at,
            "source_task_signature": "",
            "evidence_excerpt": "",
            "fingerprint": _fingerprint("manual", text),
        }
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return {"ok": True, "name": slug, "path": str(path), "created": existing is None}

    def remove_global_manual(self, name: str) -> dict[str, Any]:
        slug = _slug(name)
        path = self._global_manual_path(slug)
        if not path.exists():
            return {"ok": False, "reason": f"global memory not found: {slug}"}
        path.unlink()
        return {"ok": True, "name": slug, "removed": True}

    def _is_ephemeral(self, text: str) -> bool:
        lowered = text.lower()
        if len(text) < 18:
            return True
        if any(
            marker in lowered
            for marker in (
                "provider request failed",
                "command exited",
                "stdout",
                "stderr",
                "returncode",
                "tool result",
                "tool call",
                "verification:",
                "connection refused",
                "shell history",
                "current directory",
                "working directory",
                "who am i",
                "what shell",
                "version",
                "installed here",
                "command path",
            )
        ):
            return True
        if lowered.startswith(("rocky ", "assistant ", "result of ")):
            return True
        return False

    def _classify_candidate(self, text: str, *, source: str) -> str | None:
        lowered = text.lower()
        if _path_candidates(text):
            return "important_path"
        if any(token in lowered for token in ("must ", "must not", "never ", "only ", "without ", "do not", "should not", "keep ", "inside ")):
            return "constraint"
        if any(token in lowered for token in ("prefer ", "preferred ", "default ", "use ", "using ", "ship with", "chosen", "selected")):
            return "preference"
        if any(token in lowered for token in ("we will", "now uses", "decided", "decision", "selected", "chosen default")):
            return "decision"
        if any(token in lowered for token in ("before ", "after ", "then ", "first ", "follow ", "verify ", "always ")):
            return "workflow_rule"
        if source == "prompt" and any(
            lowered.startswith(prefix)
            for prefix in ("build ", "create ", "implement ", "add ", "make ", "fix ", "support ", "design ")
        ):
            return "goal"
        if source == "prompt" and any(token in lowered for token in ("project", "agent", "workflow", "memory", "ui", "tui")):
            return "goal"
        return None

    def _candidate_from_text(
        self,
        text: str,
        *,
        source: str,
        task_signature: str,
    ) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for raw_piece in SPLIT_RE.split(text):
            piece = _clean_text(raw_piece.strip("-*# \t"))
            if not piece or len(piece) > 240 or self._is_ephemeral(piece):
                continue
            kind = self._classify_candidate(piece, source=source)
            if kind is None:
                continue
            candidates.append(
                MemoryCandidate(
                    kind=kind,
                    title=_title_from_text(kind, piece),
                    text=piece,
                    source_task_signature=task_signature,
                    evidence_excerpt=piece[:200],
                )
            )
        return candidates

    def _candidate_from_tool_events(self, task_signature: str, tool_events: list[dict[str, Any]]) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            arguments = event.get("arguments") or {}
            path = arguments.get("path")
            if not isinstance(path, str):
                continue
            normalized = path.strip()
            if not normalized or normalized.startswith((".rocky/", ".git/")):
                continue
            if normalized in {".", ""}:
                continue
            text = f"Important project path: `{normalized}`"
            candidates.append(
                MemoryCandidate(
                    kind="important_path",
                    title=normalized,
                    text=text,
                    source_task_signature=task_signature,
                    evidence_excerpt=f"path from {event.get('name', 'tool')}",
                )
            )
        return candidates

    def _extract_auto_candidates(
        self,
        prompt: str,
        answer: str,
        task_signature: str,
        trace: dict[str, Any],
    ) -> list[MemoryCandidate]:
        all_candidates = [
            *self._candidate_from_text(prompt, source="prompt", task_signature=task_signature),
            *self._candidate_from_text(answer, source="answer", task_signature=task_signature),
            *self._candidate_from_tool_events(task_signature, trace.get("tool_events") or []),
        ]
        seen: set[str] = set()
        ranked: list[tuple[int, MemoryCandidate]] = []
        for candidate in all_candidates:
            if candidate.kind not in AUTO_KINDS:
                continue
            if candidate.fingerprint in seen:
                continue
            seen.add(candidate.fingerprint)
            score = AUTO_KIND_PRIORITY.get(candidate.kind, 0)
            if candidate.kind == "goal" and prompt.lower() in candidate.text.lower():
                score += 2
            if candidate.kind == "important_path":
                score += 1
            ranked.append((score, candidate))
        ranked.sort(key=lambda item: (item[0], len(item[1].text)), reverse=True)
        return [candidate for _, candidate in ranked[:AUTO_LIMIT]]

    def _auto_note_path(self, candidate: MemoryCandidate) -> Path:
        return self.project_auto_dir / f"{candidate.kind}-{_slug(candidate.title)}.json"

    def _existing_auto_note_by_fingerprint(self, fingerprint: str) -> MemoryNote | None:
        for note in self.load_project_auto_notes():
            if note.fingerprint == fingerprint:
                return note
        return None

    def _upsert_project_auto(self, candidate: MemoryCandidate) -> dict[str, Any]:
        existing = self._existing_auto_note_by_fingerprint(candidate.fingerprint)
        path = existing.path if existing is not None else self._auto_note_path(candidate)
        current = self._load_json_note(path, scope="project_auto", origin="auto", writable=False) if path.exists() else None
        created_at = current.created_at if current is not None else utc_iso()
        payload = {
            "id": path.stem,
            "name": path.stem,
            "title": candidate.title,
            "scope": "project_auto",
            "origin": "auto",
            "kind": candidate.kind,
            "text": candidate.text,
            "created_at": created_at,
            "updated_at": utc_iso(),
            "source_task_signature": candidate.source_task_signature,
            "evidence_excerpt": candidate.evidence_excerpt,
            "fingerprint": candidate.fingerprint,
        }
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return {"name": path.stem, "path": str(path), "updated": current is not None}

    def rebuild_project_brief(self) -> None:
        notes = self.load_project_auto_notes()
        if not notes:
            if self.project_brief_path.exists():
                self.project_brief_path.unlink()
            return
        grouped: dict[str, list[MemoryNote]] = {kind: [] for kind in AUTO_KINDS}
        for note in notes:
            grouped.setdefault(note.kind, []).append(note)
        sections = [
            "# Project Brief",
            "",
            "Automatically synthesized from Rocky's durable project memory.",
        ]
        labels = {
            "goal": "Goals",
            "constraint": "Constraints",
            "preference": "Preferences",
            "decision": "Decisions",
            "important_path": "Important Paths",
            "workflow_rule": "Workflow Rules",
        }
        for kind in ("goal", "constraint", "preference", "decision", "important_path", "workflow_rule"):
            items = grouped.get(kind) or []
            if not items:
                continue
            sections.extend(["", f"## {labels[kind]}"])
            for note in items[:8]:
                sections.append(f"- {note.text}")
        write_text(self.project_brief_path, "\n".join(sections).rstrip() + "\n")

    def capture_project_memory(
        self,
        prompt: str,
        answer: str,
        task_signature: str,
        trace: dict[str, Any],
    ) -> dict[str, Any]:
        candidates = self._extract_auto_candidates(prompt, answer, task_signature, trace)
        if not candidates:
            return {"written": 0, "notes": []}
        written = [self._upsert_project_auto(candidate) for candidate in candidates]
        self.rebuild_project_brief()
        return {"written": len(written), "notes": written}
