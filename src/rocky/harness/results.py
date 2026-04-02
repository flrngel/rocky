from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rocky.util.text import safe_json
from rocky.util.time import utc_iso


@dataclass(slots=True)
class HarnessRunRecord:
    scenario_name: str
    phase: str
    prompt: str
    route: str
    verification_status: str
    trace_path: str | None = None
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_iso)


class HarnessResultStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _phase_dir(self, phase: str) -> Path:
        target = self.root / phase
        target.mkdir(parents=True, exist_ok=True)
        return target

    def write(self, record: HarnessRunRecord) -> Path:
        stamp = record.created_at.replace(":", "").replace("-", "")
        filename = f"{stamp}__{record.scenario_name}.json"
        path = self._phase_dir(record.phase) / filename
        path.write_text(safe_json(asdict(record)) + "\n", encoding="utf-8")
        return path

    def list_recent(self, *, phase: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        roots = [self._phase_dir(phase)] if phase else [path for path in self.root.iterdir() if path.is_dir()]
        rows: list[dict[str, Any]] = []
        for directory in roots:
            for path in sorted(directory.glob("*.json"), reverse=True):
                try:
                    rows.append({"path": str(path), **self.read(path)})
                except Exception:
                    continue
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[:limit]

    def read(self, path: Path) -> dict[str, Any]:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
