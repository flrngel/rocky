from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from rocky.util.time import utc_iso


class EpisodeStore:
    def __init__(
        self,
        support_dir: Path,
        query_dir: Path,
        generation_file: Path,
        *,
        create_layout: bool = True,
    ) -> None:
        self.support_dir = support_dir
        self.query_dir = query_dir
        self.generation_file = generation_file
        if create_layout:
            self.support_dir.mkdir(parents=True, exist_ok=True)
            self.query_dir.mkdir(parents=True, exist_ok=True)
            self.generation_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.generation_file.exists():
                self.generation_file.write_text(
                    '{"current_generation": 0}\n',
                    encoding="utf-8",
                )

    def current_generation(self) -> int:
        try:
            return int(
                json.loads(self.generation_file.read_text(encoding="utf-8")).get(
                    "current_generation", 0
                )
            )
        except Exception:
            return 0

    def set_generation(self, generation: int) -> None:
        self.generation_file.parent.mkdir(parents=True, exist_ok=True)
        self.generation_file.write_text(
            json.dumps({"current_generation": generation}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

    def record_support(self, payload: dict[str, Any]) -> dict[str, Any]:
        episode_id = payload.get("id") or f"sup_{uuid.uuid4().hex[:12]}"
        record = {
            **payload,
            "id": episode_id,
            "created_at": payload.get("created_at") or utc_iso(),
        }
        path = self.support_dir / f"{episode_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {**record, "path": str(path)}

    def record_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        episode_id = payload.get("id") or f"qry_{uuid.uuid4().hex[:12]}"
        record = {
            **payload,
            "id": episode_id,
            "created_at": payload.get("created_at") or utc_iso(),
        }
        path = self.query_dir / f"{episode_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {**record, "path": str(path)}
