from __future__ import annotations

from rocky.memory.store import MemoryNote
from rocky.util.text import tokenize_keywords


class MemoryRetriever:
    def __init__(self, notes: list[MemoryNote]) -> None:
        self.notes = notes

    def retrieve(self, prompt: str, limit: int = 3) -> list[MemoryNote]:
        query_words = tokenize_keywords(prompt)
        scored: list[tuple[int, MemoryNote]] = []
        for note in self.notes:
            haystack_tokens = tokenize_keywords(note.text)
            score = len(query_words & haystack_tokens)
            if score >= 2:
                scored.append((score, note))
        scored.sort(key=lambda item: (item[0], item[1].scope == 'project'), reverse=True)
        return [note for _, note in scored[:limit]]

    def inventory(self) -> list[dict]:
        return [note.as_record() for note in self.notes]
