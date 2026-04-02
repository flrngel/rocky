from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rocky.core.messages import Message
from rocky.util.text import tokenize_keywords
from rocky.util.time import utc_iso


TURN_SUMMARY_LIMIT = 24
PATH_RE = re.compile(r"(?<![A-Za-z0-9])(?:\.{1,2}/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")


def _excerpt(text: Any, limit: int = 240) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 3)] + "..."


def _path_candidates(text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in PATH_RE.findall(text or ""):
        if "://" in match:
            continue
        cleaned = match.strip(".,:;()[]{}<>`\"'")
        if not cleaned or cleaned in seen:
            continue
        ordered.append(cleaned)
        seen.add(cleaned)
        if len(ordered) >= 12:
            break
    return ordered


def _tool_names(tool_events: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for event in tool_events:
        name = str(event.get("name") or "").strip()
        if not name or name in seen:
            continue
        ordered.append(name)
        seen.add(name)
    return ordered


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

    def append_turn_summary(self, summary: dict[str, Any], *, limit: int = TURN_SUMMARY_LIMIT) -> None:
        rows = list(self.meta.get("turn_summaries") or [])
        rows.append(summary)
        self.meta["turn_summaries"] = rows[-limit:]
        self.meta["last_turn_summary"] = summary
        self.meta["last_updated_at"] = summary.get("at", utc_iso())


class SessionStore:
    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.current_session_id_path = self.sessions_dir / ".current"
        self.current: Session | None = None

    def _path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def create(self, title: str = "session", make_current: bool = True) -> Session:
        session = Session(id=f"ses_{uuid.uuid4().hex[:10]}", created_at=utc_iso(), title=title)
        self.save(session)
        if make_current:
            self.current = session
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            self.current_session_id_path.write_text(session.id, encoding="utf-8")
        return session

    def save(self, session: Session) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._path(session.id).write_text(
            json.dumps(asdict(session), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load(self, session_id: str) -> Session:
        data = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        session = Session(**data)
        self.current = session
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
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
        for path in self.sessions_dir.glob("ses_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            meta = data.get("meta") or {}
            rows.append(
                {
                    "id": data["id"],
                    "created_at": data["created_at"],
                    "last_updated_at": meta.get("last_updated_at") or data.get("created_at"),
                    "title": data.get("title", ""),
                    "messages": len(data.get("messages", [])),
                    "last_task_signature": meta.get("last_task_signature"),
                    "last_verification": meta.get("last_verification"),
                }
            )
        rows.sort(key=lambda item: str(item.get("last_updated_at") or item.get("created_at") or ""), reverse=True)
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

    def record_turn(
        self,
        session: Session,
        *,
        prompt: str,
        answer: str,
        task_signature: str,
        verification: dict[str, Any],
        trace: dict[str, Any],
        execution_cwd: str = ".",
    ) -> dict[str, Any]:
        tool_events = trace.get("tool_events") or []
        tools = _tool_names(tool_events)
        verification_status = str(verification.get("status") or "")
        answer_excerpt = _excerpt(answer, 260)
        prompt_excerpt = _excerpt(prompt, 180)
        tool_text = "\n".join(
            str(event.get("text") or "")
            for event in tool_events[-8:]
            if isinstance(event, dict)
        )
        paths = _path_candidates("\n".join([prompt, answer, tool_text]))
        summary_lines = [
            f"[{verification_status or 'unknown'}] {task_signature} @ {execution_cwd or '.'}",
            f"task: {prompt_excerpt}",
        ]
        if tools:
            summary_lines.append(f"tools: {', '.join(tools[:6])}")
        if paths:
            summary_lines.append(f"paths: {', '.join(paths[:6])}")
        if answer_excerpt:
            summary_lines.append(f"result: {answer_excerpt}")
        keywords = sorted(
            tokenize_keywords(
                " ".join(
                    [
                        prompt,
                        answer,
                        task_signature,
                        execution_cwd or ".",
                        " ".join(tools),
                        " ".join(paths),
                    ]
                )
            )
        )[:64]
        summary = {
            "at": utc_iso(),
            "task_signature": task_signature,
            "verification": verification_status,
            "prompt": prompt_excerpt,
            "answer_excerpt": answer_excerpt,
            "tool_names": tools,
            "important_paths": paths,
            "execution_cwd": execution_cwd or ".",
            "project_keywords": keywords,
            "text": "\n".join(summary_lines),
        }
        session.append_turn_summary(summary)
        session.meta["last_task_signature"] = task_signature
        session.meta["last_verification"] = verification_status
        session.meta["last_updated_at"] = summary["at"]
        session.meta["project_keywords"] = keywords
        self.save(session)
        return summary

    def recent_turn_summaries(
        self,
        *,
        limit: int = 40,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self.sessions_dir.glob("ses_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            session_id = data.get("id")
            if exclude_session_id and session_id == exclude_session_id:
                continue
            title = data.get("title", "")
            summaries = ((data.get("meta") or {}).get("turn_summaries") or [])[-TURN_SUMMARY_LIMIT:]
            for item in summaries:
                rows.append(
                    {
                        **item,
                        "session_id": session_id,
                        "session_title": title,
                        "created_at": data.get("created_at"),
                    }
                )
        rows.sort(key=lambda item: str(item.get("at") or item.get("created_at") or ""), reverse=True)
        return rows[:limit]

    def retrieve_handoffs(
        self,
        prompt: str,
        execution_cwd: str,
        *,
        limit: int = 3,
        exclude_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        summaries = self.recent_turn_summaries(limit=40, exclude_session_id=exclude_session_id)
        if not summaries:
            return []

        preferred_seed = next(
            (
                item
                for item in summaries
                if str(item.get("execution_cwd") or ".") == execution_cwd
                and str(item.get("verification") or "") == "pass"
            ),
            None,
        )
        if preferred_seed is None:
            preferred_seed = next(
                (item for item in summaries if str(item.get("verification") or "") == "pass"),
                None,
            )
        if preferred_seed is None:
            preferred_seed = summaries[0]

        selected: list[dict[str, Any]] = [preferred_seed]
        seen_keys = {
            (
                preferred_seed.get("session_id"),
                preferred_seed.get("at"),
                preferred_seed.get("task_signature"),
            )
        }

        query_tokens = tokenize_keywords(" ".join([prompt, execution_cwd or "."]))
        scored: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
        for item in summaries:
            key = (item.get("session_id"), item.get("at"), item.get("task_signature"))
            if key in seen_keys:
                continue
            item_tokens = set(item.get("project_keywords") or [])
            item_tokens |= tokenize_keywords(
                " ".join(
                    [
                        str(item.get("prompt") or ""),
                        str(item.get("answer_excerpt") or ""),
                        str(item.get("task_signature") or ""),
                        str(item.get("execution_cwd") or "."),
                        " ".join(item.get("tool_names") or []),
                        " ".join(item.get("important_paths") or []),
                    ]
                )
            )
            overlap = len(query_tokens & item_tokens)
            same_dir = 1 if str(item.get("execution_cwd") or ".") == execution_cwd else 0
            passed = 1 if str(item.get("verification") or "") == "pass" else 0
            scored.append(((same_dir, overlap + passed, passed, str(item.get("at") or "")), item))
        scored.sort(key=lambda item: item[0], reverse=True)

        for _, item in scored:
            if len(selected) >= limit:
                break
            key = (item.get("session_id"), item.get("at"), item.get("task_signature"))
            if key in seen_keys:
                continue
            selected.append(item)
            seen_keys.add(key)
        return selected[:limit]
