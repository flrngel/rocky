from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Skill:
    name: str
    scope: str
    path: Path
    body: str
    metadata: dict[str, Any]
    origin: str = 'manual'

    @property
    def description(self) -> str:
        if self.metadata.get('description'):
            return str(self.metadata['description'])
        for line in self.body.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                return stripped[:120]
        return 'No description'

    @property
    def task_signatures(self) -> list[str]:
        return [str(item) for item in (self.metadata.get('task_signatures') or [])]

    @property
    def triggers(self) -> list[str]:
        retrieval = self.metadata.get('retrieval') or {}
        return [str(item) for item in (retrieval.get('triggers') or [])]

    @property
    def retrieval_keywords(self) -> list[str]:
        retrieval = self.metadata.get('retrieval') or {}
        keywords = [str(item) for item in (retrieval.get('keywords') or [])]
        keywords.extend(str(item) for item in (self.metadata.get('paths') or []))
        keywords.extend(str(item) for item in (self.metadata.get('tools') or []))
        return keywords

    @property
    def generation(self) -> int:
        try:
            return int(self.metadata.get('generation', 0))
        except Exception:
            return 0

    def as_record(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'scope': self.scope,
            'origin': self.origin,
            'generation': self.generation,
            'description': self.description,
            'path': str(self.path),
        }
