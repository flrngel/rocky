from __future__ import annotations

import re

from rocky.memory.store import MemoryNote


class MemoryRetriever:
    def __init__(self, notes: list[MemoryNote]) -> None:
        self.notes = notes

    def retrieve(self, prompt: str, limit: int = 3) -> list[MemoryNote]:
        query_words = {word for word in re.findall(r'[a-zA-Z0-9_\-]+', prompt.lower()) if len(word) > 2}
        scored: list[tuple[int, MemoryNote]] = []
        for note in self.notes:
            haystack = note.text.lower()
            score = sum(1 for word in query_words if word in haystack)
            if score:
                scored.append((score, note))
        scored.sort(key=lambda item: (item[0], item[1].scope == 'project'), reverse=True)
        return [note for _, note in scored[:limit]]

    def inventory(self) -> list[dict]:
        return [note.as_record() for note in self.notes]
