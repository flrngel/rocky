from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from rocky.core.runtime_state import Claim
from rocky.util.io import read_text, write_text
from rocky.util.text import tokenize_keywords
from rocky.util.time import utc_iso


AUTO_LIMIT = 6
AUTO_KINDS = {
    "goal",
    "constraint",
    "preference",
    "decision",
    "important_path",
    "workflow_rule",
    "project_fact",
}
AUTO_KIND_PRIORITY = {
    "goal": 6,
    "constraint": 5,
    "preference": 4,
    "decision": 4,
    "important_path": 6,
    "workflow_rule": 5,
    "project_fact": 5,
    "project_brief": 10,
    "manual": 1,
}
PATH_RE = re.compile(r"(?:^|[\s`'\"(])((?:\./|\.rocky/|src/|tests/|docs/|README\.md|[A-Za-z0-9._-]+/)[^\s`'\"()]+)")
SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


PROVENANCE_PROMOTION_SCORE = {
    "tool_observed": 4,
    "user_asserted": 4,
    "learned_rule": 3,
    "agent_inferred": 1,
}


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
    provenance_type: str = "user_asserted"
    supporting_claim_ids: list[str] = field(default_factory=list)
    contradiction_state: str = "active"
    promotion_state: str = "promoted"
    thread_id: str | None = None
    stability_score: float = 0.5
    reusability_score: float = 0.5

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
            "provenance_type": self.provenance_type,
            "contradiction_state": self.contradiction_state,
            "promotion_state": self.promotion_state,
            "thread_id": self.thread_id,
            "stability_score": self.stability_score,
            "reusability_score": self.reusability_score,
        }

    def keyword_text(self) -> str:
        return " ".join(part for part in (self.title, self.text, self.kind, self.name, self.source_task_signature) if part)


@dataclass(slots=True)
class CandidateMemory:
    candidate_id: str
    thread_id: str | None
    scope: str
    kind: str
    text: str
    provenance_type: str
    supporting_claim_ids: list[str]
    contradiction_state: str
    stability_score: float
    reusability_score: float
    promotion_state: str
    source_task_signature: str
    evidence_excerpt: str
    title: str
    created_at: str = field(default_factory=utc_iso)
    updated_at: str = field(default_factory=utc_iso)

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.kind, self.text)

    def as_record(self) -> dict[str, Any]:
        return {
            "id": self.candidate_id,
            "candidate_id": self.candidate_id,
            "thread_id": self.thread_id,
            "scope": self.scope,
            "kind": self.kind,
            "text": self.text,
            "provenance_type": self.provenance_type,
            "supporting_claim_ids": self.supporting_claim_ids,
            "contradiction_state": self.contradiction_state,
            "stability_score": self.stability_score,
            "reusability_score": self.reusability_score,
            "promotion_state": self.promotion_state,
            "source_task_signature": self.source_task_signature,
            "evidence_excerpt": self.evidence_excerpt,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "fingerprint": self.fingerprint,
        }


class MemoryStore:
    def __init__(self, project_dir: Path, global_dir: Path, *, create_layout: bool = True) -> None:
        self.project_dir = project_dir
        self.global_dir = global_dir
        self.project_auto_dir = project_dir / "auto"
        self.project_candidate_dir = project_dir / "candidates"
        self.project_brief_path = project_dir / "project_brief.md"
        self.global_manual_dir = global_dir / "global"
        if create_layout:
            self.ensure_layout()

    def ensure_layout(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.project_auto_dir.mkdir(parents=True, exist_ok=True)
        self.project_candidate_dir.mkdir(parents=True, exist_ok=True)
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
            provenance_type="learned_rule",
            contradiction_state="active",
            promotion_state="promoted",
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
            provenance_type=str(payload.get("provenance_type") or ("user_asserted" if scope == "global_manual" else "tool_observed")),
            supporting_claim_ids=[str(item) for item in (payload.get("supporting_claim_ids") or [])],
            contradiction_state=str(payload.get("contradiction_state") or "active"),
            promotion_state=str(payload.get("promotion_state") or ("promoted" if scope != "project_candidate" else "candidate")),
            thread_id=str(payload.get("thread_id") or "") or None,
            stability_score=float(payload.get("stability_score") or 0.5),
            reusability_score=float(payload.get("reusability_score") or 0.5),
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
            provenance_type="user_asserted",
            contradiction_state="active",
            promotion_state="promoted",
            stability_score=1.0,
            reusability_score=1.0,
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
        notes.sort(key=lambda item: (item.updated_at, item.stability_score, item.name), reverse=True)
        return notes

    def load_project_candidate_notes(self) -> list[MemoryNote]:
        notes: list[MemoryNote] = []
        if not self.project_candidate_dir.exists():
            return notes
        for path in sorted(self.project_candidate_dir.glob("*.json")):
            note = self._load_json_note(path, scope="project_candidate", origin="auto", writable=False)
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
        notes.extend(self.load_project_candidate_notes())
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
            "source_task_signature": existing.source_task_signature if existing is not None else "",
            "evidence_excerpt": existing.evidence_excerpt if existing is not None else "",
            "fingerprint": _fingerprint("manual", text),
            "provenance_type": "user_asserted",
            "supporting_claim_ids": existing.supporting_claim_ids if existing is not None else [],
            "contradiction_state": "active",
            "promotion_state": "promoted",
            "stability_score": 1.0,
            "reusability_score": 1.0,
        }
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return {"ok": True, "name": slug, "path": str(path), "updated": existing is not None}

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

    def _classify_text(self, text: str, *, source: str) -> str | None:
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
        if source == "claim" and any(token in lowered for token in ("observed", "read file", "created or updated file", "path")):
            return "project_fact"
        if source == "prompt" and any(lowered.startswith(prefix) for prefix in ("build ", "create ", "implement ", "add ", "make ", "fix ", "support ", "design ")):
            return "goal"
        if source == "prompt" and any(token in lowered for token in ("project", "agent", "workflow", "memory", "ui", "tui")):
            return "goal"
        return None

    def _candidate_from_supported_claim(self, claim: Claim, *, task_signature: str) -> CandidateMemory | None:
        cleaned = _clean_text(claim.text)
        if not cleaned or self._is_ephemeral(cleaned):
            return None
        kind = self._classify_text(cleaned, source="claim")
        if kind is None:
            return None
        stability = min(1.0, 0.4 + 0.12 * PROVENANCE_PROMOTION_SCORE.get(claim.provenance_type, 0) + 0.15 * claim.confidence)
        if kind == "important_path":
            stability = max(stability, 0.9)
        reusability = 0.45 + 0.1 * (1 if kind in {"constraint", "workflow_rule", "important_path", "preference"} else 0)
        contradiction_state = "disputed" if claim.status == "disputed" else "active"
        return CandidateMemory(
            candidate_id=f"cand-{_slug(kind + '-' + _title_from_text(kind, cleaned))}",
            thread_id=claim.thread_id,
            scope="project_auto",
            kind=kind,
            text=cleaned,
            provenance_type=claim.provenance_type,
            supporting_claim_ids=[claim.claim_id],
            contradiction_state=contradiction_state,
            stability_score=stability,
            reusability_score=reusability,
            promotion_state="candidate",
            source_task_signature=task_signature,
            evidence_excerpt=cleaned[:200],
            title=_title_from_text(kind, cleaned),
        )

    def _candidate_from_prompt(self, prompt: str, *, task_signature: str, thread_id: str | None = None) -> list[CandidateMemory]:
        candidates: list[CandidateMemory] = []
        for raw_piece in SPLIT_RE.split(prompt):
            piece = _clean_text(raw_piece.strip("-*# \t"))
            if not piece or len(piece) > 240 or self._is_ephemeral(piece):
                continue
            kind = self._classify_text(piece, source="prompt")
            if kind is None:
                continue
            candidates.append(
                CandidateMemory(
                    candidate_id=f"cand-{_slug(kind + '-' + _title_from_text(kind, piece))}",
                    thread_id=thread_id,
                    scope="project_auto",
                    kind=kind,
                    text=piece,
                    provenance_type="user_asserted",
                    supporting_claim_ids=[],
                    contradiction_state="active",
                    stability_score=0.8 if kind in {"constraint", "preference", "goal"} else 0.7,
                    reusability_score=0.7,
                    promotion_state="candidate",
                    source_task_signature=task_signature,
                    evidence_excerpt=piece[:200],
                    title=_title_from_text(kind, piece),
                )
            )
        return candidates

    def _candidate_from_tool_events(self, task_signature: str, tool_events: list[dict[str, Any]], *, thread_id: str | None = None) -> list[CandidateMemory]:
        candidates: list[CandidateMemory] = []
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            arguments = event.get("arguments") or {}
            path = arguments.get("path")
            if not isinstance(path, str):
                continue
            normalized = path.strip()
            if not normalized or normalized.startswith((".rocky/", ".git/")) or normalized in {".", ""}:
                continue
            text = f"Important project path: `{normalized}`"
            candidates.append(
                CandidateMemory(
                    candidate_id=f"cand-{_slug('important-path-' + normalized)}",
                    thread_id=thread_id,
                    scope="project_auto",
                    kind="important_path",
                    text=text,
                    provenance_type="tool_observed",
                    supporting_claim_ids=[],
                    contradiction_state="active",
                    stability_score=0.95,
                    reusability_score=0.75,
                    promotion_state="candidate",
                    source_task_signature=task_signature,
                    evidence_excerpt=f"path from {event.get('name', 'tool')}",
                    title=normalized,
                )
            )
        return candidates

    def _extract_candidates(
        self,
        prompt: str,
        answer: str,
        task_signature: str,
        trace: dict[str, Any],
        *,
        supported_claims: list[dict[str, Any]] | list[Claim] | None = None,
        thread_id: str | None = None,
    ) -> list[CandidateMemory]:
        claims: list[Claim] = []
        for item in supported_claims or []:
            if isinstance(item, Claim):
                claims.append(item)
            elif isinstance(item, dict) and item.get("claim_id") and item.get("text"):
                claims.append(
                    Claim(
                        claim_id=str(item.get("claim_id")),
                        thread_id=str(item.get("thread_id") or thread_id or ""),
                        text=str(item.get("text")),
                        provenance_type=str(item.get("provenance_type") or "tool_observed"),
                        provenance_source=str(item.get("provenance_source") or "runtime"),
                        confidence=float(item.get("confidence") or 0.7),
                        support_refs=[str(x) for x in (item.get("support_refs") or [])],
                        contradiction_refs=[str(x) for x in (item.get("contradiction_refs") or [])],
                        status=str(item.get("status") or "active"),
                        created_at=str(item.get("created_at") or utc_iso()),
                    )
                )
        all_candidates: list[CandidateMemory] = []
        if claims:
            all_candidates.extend(
                candidate
                for claim in claims
                if claim.provenance_type in {"tool_observed", "user_asserted", "learned_rule"}
                for candidate in [self._candidate_from_supported_claim(claim, task_signature=task_signature)]
                if candidate is not None
            )
        else:
            # compatibility fallback: old behavior but prompt/tool anchored only, never answer text first.
            all_candidates.extend(self._candidate_from_prompt(prompt, task_signature=task_signature, thread_id=thread_id))
        all_candidates.extend(self._candidate_from_tool_events(task_signature, trace.get("tool_events") or [], thread_id=thread_id))
        seen: set[str] = set()
        ranked: list[tuple[float, CandidateMemory]] = []
        for candidate in all_candidates:
            if candidate.kind not in AUTO_KINDS:
                continue
            if candidate.fingerprint in seen:
                continue
            seen.add(candidate.fingerprint)
            score = AUTO_KIND_PRIORITY.get(candidate.kind, 0) + candidate.stability_score + candidate.reusability_score
            if candidate.contradiction_state != "active":
                score -= 2
            ranked.append((score, candidate))
        ranked.sort(key=lambda item: (item[0], len(item[1].text)), reverse=True)
        return [candidate for _, candidate in ranked[:AUTO_LIMIT]]

    def _candidate_path(self, candidate: CandidateMemory) -> Path:
        return self.project_candidate_dir / f"{_slug(candidate.kind + '-' + candidate.title)}.json"

    def _auto_note_path(self, candidate: CandidateMemory) -> Path:
        return self.project_auto_dir / f"{candidate.kind}-{_slug(candidate.title)}.json"

    def _existing_auto_note_by_fingerprint(self, fingerprint: str) -> MemoryNote | None:
        for note in self.load_project_auto_notes():
            if note.fingerprint == fingerprint:
                return note
        return None

    def _existing_candidate_by_fingerprint(self, fingerprint: str) -> MemoryNote | None:
        for note in self.load_project_candidate_notes():
            if note.fingerprint == fingerprint:
                return note
        return None

    def _write_candidate(self, candidate: CandidateMemory) -> dict[str, Any]:
        path = self._candidate_path(candidate)
        existing = self._existing_candidate_by_fingerprint(candidate.fingerprint)
        if existing is not None:
            path = existing.path
        payload = candidate.as_record()
        payload["name"] = path.stem
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return {"name": path.stem, "path": str(path), "promotion_state": candidate.promotion_state}

    def _contradiction_state_for_candidate(self, candidate: CandidateMemory) -> str:
        related = [
            note
            for note in self.load_project_auto_notes()
            if note.kind == candidate.kind and note.title == candidate.title and note.fingerprint != candidate.fingerprint
        ]
        if not related:
            return candidate.contradiction_state
        strongest = max(
            related,
            key=lambda item: (PROVENANCE_PROMOTION_SCORE.get(item.provenance_type, 0), item.stability_score, item.updated_at),
        )
        if strongest.text != candidate.text:
            if PROVENANCE_PROMOTION_SCORE.get(candidate.provenance_type, 0) > PROVENANCE_PROMOTION_SCORE.get(strongest.provenance_type, 0):
                strongest_payload = json.loads(read_text(strongest.path))
                strongest_payload["contradiction_state"] = "superseded"
                strongest_payload["updated_at"] = utc_iso()
                write_text(strongest.path, json.dumps(strongest_payload, ensure_ascii=False, indent=2) + "\n")
                return "active"
            return "disputed"
        return candidate.contradiction_state

    def _should_promote(self, candidate: CandidateMemory) -> bool:
        if candidate.provenance_type not in {"tool_observed", "user_asserted", "learned_rule"}:
            return False
        if candidate.contradiction_state not in {"active", "superseded"}:
            return False
        if candidate.kind in {"important_path", "constraint", "workflow_rule", "preference", "project_fact"}:
            return candidate.stability_score >= 0.7
        if candidate.kind == "goal":
            return candidate.provenance_type == "user_asserted" and candidate.stability_score >= 0.75
        return candidate.stability_score >= 0.8

    def _upsert_project_auto(self, candidate: CandidateMemory) -> dict[str, Any]:
        existing = self._existing_auto_note_by_fingerprint(candidate.fingerprint)
        path = existing.path if existing is not None else self._auto_note_path(candidate)
        current = self._load_json_note(path, scope="project_auto", origin="auto", writable=False) if path.exists() else None
        created_at = current.created_at if current is not None else utc_iso()
        contradiction_state = self._contradiction_state_for_candidate(candidate)
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
            "provenance_type": candidate.provenance_type,
            "supporting_claim_ids": candidate.supporting_claim_ids,
            "contradiction_state": contradiction_state,
            "promotion_state": "promoted",
            "thread_id": candidate.thread_id,
            "stability_score": candidate.stability_score,
            "reusability_score": candidate.reusability_score,
        }
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return {"name": path.stem, "path": str(path), "updated": current is not None, "contradiction_state": contradiction_state}

    def rebuild_project_brief(self) -> None:
        notes = [
            note
            for note in self.load_project_auto_notes()
            if note.contradiction_state == "active" and note.promotion_state == "promoted"
        ]
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
            "Synthesized only from durable evidence-backed memory, explicit user corrections/preferences, and promoted learned workflow notes.",
        ]
        labels = {
            "goal": "Goals",
            "constraint": "Constraints",
            "preference": "Preferences",
            "decision": "Decisions",
            "important_path": "Important Paths",
            "workflow_rule": "Workflow Rules",
            "project_fact": "Confirmed Project Facts",
        }
        for kind in ("goal", "constraint", "preference", "decision", "important_path", "workflow_rule", "project_fact"):
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
        *,
        supported_claims: list[dict[str, Any]] | list[Claim] | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        candidates = self._extract_candidates(
            prompt,
            answer,
            task_signature,
            trace,
            supported_claims=supported_claims,
            thread_id=thread_id,
        )
        if not candidates:
            return {"written": 0, "candidates": [], "notes": []}
        candidate_records = [self._write_candidate(candidate) for candidate in candidates]
        promoted: list[dict[str, Any]] = []
        for candidate in candidates:
            if self._should_promote(candidate):
                promoted.append(self._upsert_project_auto(candidate))
        self.rebuild_project_brief()
        return {"written": len(promoted), "candidates": candidate_records, "notes": promoted}
