from __future__ import annotations

from rocky.memory.store import AUTO_KIND_PRIORITY, MemoryNote
from rocky.util.text import tokenize_keywords


class MemoryRetriever:
    def __init__(self, notes: list[MemoryNote]) -> None:
        self.notes = notes

    def project_brief(self) -> MemoryNote | None:
        for note in self.notes:
            if note.kind == "project_brief" and note.scope == "project_auto":
                return note
        return None

    def retrieve(self, prompt: str, limit: int = 3) -> list[MemoryNote]:
        query_words = tokenize_keywords(prompt)
        scored: list[tuple[tuple[int, int, int, str], MemoryNote]] = []
        for note in self.notes:
            if note.kind == "project_brief":
                continue
            haystack_tokens = tokenize_keywords(note.keyword_text())
            overlap = len(query_words & haystack_tokens)
            min_overlap = 1 if note.scope == "project_auto" else 2
            if overlap < min_overlap:
                continue
            scope_weight = 2 if note.scope == "project_auto" else 1
            kind_weight = AUTO_KIND_PRIORITY.get(note.kind, 0)
            scored.append(((scope_weight, overlap + kind_weight, kind_weight, note.updated_at), note))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [note for _, note in scored[:limit]]

    def inventory(self) -> list[dict]:
        return [note.as_record() for note in self.notes]
