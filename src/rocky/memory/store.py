from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rocky.util.io import read_text


@dataclass(slots=True)
class MemoryNote:
    path: Path
    scope: str
    text: str

    @property
    def name(self) -> str:
        return self.path.stem

    def as_record(self) -> dict:
        return {'name': self.name, 'scope': self.scope, 'path': str(self.path)}


class MemoryStore:
    def __init__(self, project_dir: Path, global_dir: Path) -> None:
        self.project_dir = project_dir
        self.global_dir = global_dir

    def load_all(self) -> list[MemoryNote]:
        notes: list[MemoryNote] = []
        for scope, root in [('project', self.project_dir), ('global', self.global_dir)]:
            if not root.exists():
                continue
            for path in sorted(root.rglob('*')):
                if path.is_file() and path.suffix.lower() in {'.md', '.txt', '.yaml', '.yml', '.json'}:
                    try:
                        notes.append(MemoryNote(path=path, scope=scope, text=read_text(path)))
                    except Exception:
                        continue
        return notes
