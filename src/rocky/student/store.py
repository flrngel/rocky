from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from rocky.config.models import RetrievalConfig
from rocky.core.runtime_state import ActiveTaskThread
from rocky.util.io import read_text, write_text
from rocky.util.text import tokenize_keywords
from rocky.util.time import utc_iso
from rocky.util.yamlx import dump_yaml, load_yaml


FRONTMATTER_START = "---\n"
FRONTMATTER_END = "\n---\n"


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return cleaned[:64] or "note"


def _note_id(prefix: str, title: str) -> str:
    digest = hashlib.sha1(f"{prefix}:{title}:{utc_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


@dataclass(slots=True)
class StudentNote:
    id: str
    kind: str
    title: str
    text: str
    path: Path
    created_at: str
    updated_at: str
    prompt: str = ""
    answer: str = ""
    feedback: str = ""
    task_signature: str = ""
    thread_id: str | None = None
    failure_class: str | None = None
    tags: list[str] = field(default_factory=list)
    origin: str = "teacher"

    def as_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "path": str(self.path),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "task_signature": self.task_signature,
            "thread_id": self.thread_id,
            "failure_class": self.failure_class,
            "tags": list(self.tags),
            "origin": self.origin,
        }

    def keyword_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.kind,
                self.title,
                self.text,
                self.prompt,
                self.answer,
                self.feedback,
                self.task_signature,
                " ".join(self.tags),
            )
            if part
        )


def _parse_markdown_note(path: Path) -> StudentNote | None:
    if not path.exists():
        return None
    text = read_text(path)
    meta: dict[str, Any] = {}
    body = text
    if text.startswith(FRONTMATTER_START):
        try:
            rest = text[len(FRONTMATTER_START):]
            fm, body = rest.split(FRONTMATTER_END, 1)
            loaded = load_yaml(fm)
            if isinstance(loaded, dict):
                meta = loaded
        except Exception:
            body = text
    return StudentNote(
        id=str(meta.get("id") or path.stem),
        kind=str(meta.get("kind") or "knowledge"),
        title=str(meta.get("title") or path.stem.replace("-", " ")),
        text=body.strip(),
        path=path,
        created_at=str(meta.get("created_at") or ""),
        updated_at=str(meta.get("updated_at") or ""),
        prompt=str(meta.get("prompt") or ""),
        answer=str(meta.get("answer") or ""),
        feedback=str(meta.get("feedback") or ""),
        task_signature=str(meta.get("task_signature") or ""),
        thread_id=str(meta.get("thread_id") or "") or None,
        failure_class=str(meta.get("failure_class") or "") or None,
        tags=[str(item) for item in (meta.get("tags") or [])],
        origin=str(meta.get("origin") or "teacher"),
    )


class StudentStore:
    _LEGACY_DEFAULT_LIMIT = 5

    def __init__(
        self,
        root: Path,
        *,
        create_layout: bool = True,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.root = root
        self.readme_path = root / "README.md"
        self.profile_path = root / "profile.md"
        self.notebook_path = root / "notebook.jsonl"
        self.knowledge_dir = root / "knowledge"
        self.patterns_dir = root / "patterns"
        self.examples_dir = root / "examples"
        self.retrospectives_dir = root / "retrospectives"
        # Phase 3 T3 (limit-narrowed): when an active meta-variant supplies a
        # `RetrievalConfig` overlay, top-K is sourced from `config.top_k_limit`.
        # Without an overlay, the legacy default (5) is preserved bit-identically.
        self.config = config
        if create_layout:
            self.ensure_layout()

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.patterns_dir.mkdir(parents=True, exist_ok=True)
        self.examples_dir.mkdir(parents=True, exist_ok=True)
        self.retrospectives_dir.mkdir(parents=True, exist_ok=True)
        if not self.readme_path.exists():
            write_text(
                self.readme_path,
                "# Rocky student notebook\n\n"
                "This directory stores teacher feedback, durable domain notes, compact self-retrospectives, patterns, and examples.\n",
            )
        if not self.profile_path.exists():
            write_text(
                self.profile_path,
                FRONTMATTER_START
                + dump_yaml(
                    {
                        "id": "student-profile",
                        "kind": "profile",
                        "title": "Student profile",
                        "created_at": utc_iso(),
                        "updated_at": utc_iso(),
                        "origin": "system",
                    }
                )
                + FRONTMATTER_END
                + "Rocky is a teachable student agent. Prefer durable teacher feedback over generic heuristics.\n",
            )
        if not self.notebook_path.exists():
            write_text(self.notebook_path, "")

    def profile(self) -> dict[str, Any]:
        note = _parse_markdown_note(self.profile_path)
        if note is None:
            return {}
        return {
            "id": note.id,
            "kind": note.kind,
            "title": note.title,
            "text": note.text,
            "updated_at": note.updated_at,
        }

    def load_all(self) -> list[StudentNote]:
        notes: list[StudentNote] = []
        profile = _parse_markdown_note(self.profile_path)
        if profile is not None:
            notes.append(profile)
        for directory in (self.knowledge_dir, self.retrospectives_dir, self.patterns_dir, self.examples_dir):
            for path in sorted(directory.glob("*.md")):
                note = _parse_markdown_note(path)
                if note is not None:
                    notes.append(note)
        if self.notebook_path.exists():
            for raw_line in self.notebook_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                notes.append(
                    StudentNote(
                        id=str(payload.get("id") or _note_id("lesson", payload.get("title") or "lesson")),
                        kind=str(payload.get("kind") or "lesson"),
                        title=str(payload.get("title") or "Teacher lesson"),
                        text=str(payload.get("text") or payload.get("feedback") or ""),
                        path=self.notebook_path,
                        created_at=str(payload.get("created_at") or ""),
                        updated_at=str(payload.get("updated_at") or payload.get("created_at") or ""),
                        prompt=str(payload.get("prompt") or ""),
                        answer=str(payload.get("answer") or ""),
                        feedback=str(payload.get("feedback") or ""),
                        task_signature=str(payload.get("task_signature") or ""),
                        thread_id=str(payload.get("thread_id") or "") or None,
                        failure_class=str(payload.get("failure_class") or "") or None,
                        tags=[str(item) for item in (payload.get("tags") or [])],
                        origin=str(payload.get("origin") or "teacher"),
                    )
                )
        notes.sort(key=lambda item: (item.updated_at, item.created_at, item.id), reverse=True)
        return notes

    def status(self) -> dict[str, Any]:
        all_notes = self.load_all()
        by_kind: dict[str, int] = {}
        for note in all_notes:
            by_kind[note.kind] = by_kind.get(note.kind, 0) + 1
        return {
            "root": str(self.root),
            "count": len(all_notes),
            "by_kind": by_kind,
            "profile": self.profile().get("title"),
        }

    def inventory(self, kind: str | None = None) -> dict[str, Any]:
        notes = self.load_all()
        if kind:
            notes = [note for note in notes if note.kind == kind]
        return {"notes": [note.as_record() for note in notes]}

    def get(self, entry_id: str) -> dict[str, Any] | None:
        for note in self.load_all():
            if note.id == entry_id:
                return {
                    **note.as_record(),
                    "text": note.text,
                    "prompt": note.prompt,
                    "answer": note.answer,
                    "feedback": note.feedback,
                }
        return None

    def _markdown_path_for_kind(self, kind: str, title: str) -> Path:
        if kind == "profile":
            return self.profile_path
        if kind == "pattern":
            return self.patterns_dir / f"{_slug(title)}.md"
        if kind == "example":
            return self.examples_dir / f"{_slug(title)}.md"
        if kind == "retrospective":
            return self.retrospectives_dir / f"{_slug(title)}.md"
        return self.knowledge_dir / f"{_slug(title)}.md"

    def add(
        self,
        kind: str,
        title: str,
        text: str,
        *,
        prompt: str = "",
        answer: str = "",
        feedback: str = "",
        task_signature: str = "",
        thread_id: str | None = None,
        failure_class: str | None = None,
        tags: list[str] | None = None,
        origin: str = "teacher",
    ) -> dict[str, Any]:
        now = utc_iso()
        if kind == "lesson":
            entry = {
                "id": _note_id("lesson", title),
                "kind": kind,
                "title": title,
                "text": text,
                "prompt": prompt,
                "answer": answer,
                "feedback": feedback,
                "task_signature": task_signature,
                "thread_id": thread_id,
                "failure_class": failure_class,
                "tags": list(tags or []),
                "origin": origin,
                "created_at": now,
                "updated_at": now,
            }
            existing = self.notebook_path.read_text(encoding="utf-8", errors="replace") if self.notebook_path.exists() else ""
            write_text(self.notebook_path, existing + json.dumps(entry, ensure_ascii=False) + "\n")
            return {"ok": True, "entry": entry, "path": str(self.notebook_path)}

        path = self._markdown_path_for_kind(kind, title)
        note_id = "student-profile" if kind == "profile" else _note_id(kind, title)
        if path.exists() and kind in {"knowledge", "retrospective", "pattern", "example"}:
            existing = _parse_markdown_note(path)
            if existing is not None:
                note_id = existing.id
                now_created = existing.created_at or now
            else:
                now_created = now
        else:
            now_created = now
        payload = {
            "id": note_id,
            "kind": kind,
            "title": title,
            "created_at": now_created,
            "updated_at": now,
            "prompt": prompt,
            "answer": answer,
            "feedback": feedback,
            "task_signature": task_signature,
            "thread_id": thread_id,
            "failure_class": failure_class,
            "tags": list(tags or []),
            "origin": origin,
        }
        write_text(path, FRONTMATTER_START + dump_yaml(payload) + FRONTMATTER_END + text.strip() + "\n")
        return {"ok": True, "entry": {**payload, "path": str(path)}, "path": str(path)}

    def record_feedback(
        self,
        feedback: str,
        *,
        prompt: str | None = None,
        answer: str | None = None,
        task_signature: str | None = None,
        thread_id: str | None = None,
        failure_class: str | None = None,
    ) -> dict[str, Any]:
        cleaned = feedback.strip()
        if not cleaned:
            return {"ok": False, "reason": "feedback is required"}
        title = cleaned.splitlines()[0][:80]
        return self.add(
            "lesson",
            title,
            cleaned,
            prompt=prompt or "",
            answer=answer or "",
            feedback=cleaned,
            task_signature=task_signature or "",
            thread_id=thread_id,
            failure_class=failure_class,
            tags=[tag for tag in (task_signature or "").replace("/", " ").split() if tag][:4],
            origin="teacher_feedback",
        )

    def retrieve(
        self,
        prompt: str,
        *,
        task_signature: str = "",
        thread: ActiveTaskThread | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if limit is None:
            limit = (
                self.config.top_k_limit
                if self.config is not None
                else self._LEGACY_DEFAULT_LIMIT
            )
        query_tokens = tokenize_keywords(prompt)
        thread_tokens = tokenize_keywords(thread.summary_text()) if thread is not None else set()
        task_tokens = tokenize_keywords(task_signature.replace("/", " ")) if task_signature else set()
        scored: list[tuple[tuple[float, float, str], StudentNote]] = []
        for note in self.load_all():
            if note.kind == "profile":
                continue
            haystack = tokenize_keywords(note.keyword_text())
            overlap = len(query_tokens & haystack)
            thread_overlap = len(thread_tokens & haystack)
            task_overlap = len(task_tokens & haystack)
            if overlap == 0 and thread_overlap == 0 and task_overlap == 0:
                continue
            kind_weight = {
                "pattern": 5,
                "retrospective": 4,
                "knowledge": 4,
                "example": 3,
                "lesson": 3,
            }.get(note.kind, 1)
            same_thread = 2 if thread is not None and note.thread_id and note.thread_id == thread.thread_id else 0
            exact_task = 2 if task_signature and note.task_signature == task_signature else 0
            score = (
                overlap + thread_overlap + task_overlap + kind_weight + same_thread + exact_task,
                kind_weight + same_thread + exact_task,
                note.updated_at,
            )
            scored.append((score, note))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                **note.as_record(),
                "text": note.text[:4000],
                "prompt": note.prompt[:1200],
                "answer": note.answer[:1200],
                "feedback": note.feedback[:1200],
            }
            for _, note in scored[:limit]
        ]
