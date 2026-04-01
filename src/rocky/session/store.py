from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rocky.core.messages import Message
from rocky.util.time import utc_iso


@dataclass(slots=True)
class Session:
    id: str
    created_at: str
    title: str = "session"
    messages: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def append(self, role: str, content: Any, **extra: Any) -> None:
        self.messages.append({"role": role, "content": content, "at": utc_iso(), **extra})

    def recent_messages(self, limit: int = 12) -> list[Message]:
        rows = self.messages[-limit:]
        return [Message(role=row["role"], content=row["content"]) for row in rows]


class SessionStore:
    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.current_session_id_path = self.sessions_dir / ".current"
        self.current: Session | None = None

    def _path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def create(self, title: str = "session") -> Session:
        session = Session(id=f"ses_{uuid.uuid4().hex[:10]}", created_at=utc_iso(), title=title)
        self.save(session)
        self.current = session
        self.current_session_id_path.write_text(session.id, encoding="utf-8")
        return session

    def save(self, session: Session) -> None:
        self._path(session.id).write_text(
            json.dumps(asdict(session), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load(self, session_id: str) -> Session:
        data = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        session = Session(**data)
        self.current = session
        self.current_session_id_path.write_text(session.id, encoding="utf-8")
        return session

    def ensure_current(self) -> Session:
        if self.current:
            return self.current
        if self.current_session_id_path.exists():
            sid = self.current_session_id_path.read_text(encoding="utf-8").strip()
            if sid and self._path(sid).exists():
                return self.load(sid)
        return self.create()

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.sessions_dir.glob("ses_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows.append(
                {
                    "id": data["id"],
                    "created_at": data["created_at"],
                    "title": data.get("title", ""),
                    "messages": len(data.get("messages", [])),
                }
            )
        return rows

    def compact(self) -> dict[str, Any]:
        session = self.ensure_current()
        if len(session.messages) <= 20:
            return {"compacted": False, "reason": "session already small"}
        kept = session.messages[-20:]
        removed = len(session.messages) - len(kept)
        session.messages = kept
        self.save(session)
        return {"compacted": True, "removed_messages": removed, "remaining_messages": len(kept)}
