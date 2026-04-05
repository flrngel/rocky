from __future__ import annotations

from rocky.core.runtime_state import ActiveTaskThread
from rocky.memory.store import AUTO_KIND_PRIORITY, MemoryNote
from rocky.util.text import tokenize_keywords


PROVENANCE_WEIGHT = {
    "tool_observed": 4,
    "user_asserted": 4,
    "learned_rule": 3,
    "agent_inferred": 1,
}

CONTRADICTION_PENALTY = {
    "active": 0,
    "none": 0,
    "superseded": 4,
    "stale": 5,
    "disputed": 6,
}


class MemoryRetriever:
    def __init__(self, notes: list[MemoryNote]) -> None:
        self.notes = notes

    def project_brief(self) -> MemoryNote | None:
        for note in self.notes:
            if note.kind == "project_brief" and note.scope == "project_auto":
                return note
        return None

    def retrieve(
        self,
        prompt: str,
        *,
        task_signature: str = "",
        thread: ActiveTaskThread | None = None,
        limit: int = 4,
    ) -> list[MemoryNote]:
        query_words = tokenize_keywords(prompt)
        thread_tokens = tokenize_keywords(thread.summary_text()) if thread is not None else set()
        scored: list[tuple[tuple[float, float, float, str], MemoryNote]] = []
        for note in self.notes:
            if note.kind == "project_brief":
                continue
            if note.scope == "project_candidate":
                # Candidate notes are useful for debugging but not default runtime context.
                continue
            haystack_tokens = tokenize_keywords(note.keyword_text())
            overlap = len(query_words & haystack_tokens)
            thread_overlap = len(thread_tokens & haystack_tokens)
            if overlap < (1 if note.scope == "project_auto" else 2) and thread_overlap == 0:
                continue
            scope_weight = 2 if note.scope == "project_auto" else 1
            kind_weight = AUTO_KIND_PRIORITY.get(note.kind, 0)
            task_bonus = 2 if task_signature and note.source_task_signature == task_signature else 0
            provenance = PROVENANCE_WEIGHT.get(note.provenance_type, 0)
            contradiction_penalty = CONTRADICTION_PENALTY.get(note.contradiction_state, 0)
            if contradiction_penalty >= 6:
                continue
            score = (
                scope_weight,
                overlap + thread_overlap + kind_weight + task_bonus + provenance - contradiction_penalty + note.stability_score,
                provenance + note.reusability_score,
                note.updated_at,
            )
            scored.append((score, note))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [note for _, note in scored[:limit]]

    def inventory(self) -> list[dict]:
        return [note.as_record() for note in self.notes]
