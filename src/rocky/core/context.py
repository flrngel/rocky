from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rocky.memory.retriever import MemoryRetriever
from rocky.session.store import SessionStore
from rocky.skills.retriever import SkillRetriever
from rocky.util.io import read_text


@dataclass(slots=True)
class ContextPackage:
    instructions: list[dict]
    memories: list[dict]
    skills: list[dict]
    tool_families: list[str]
    workspace_focus: dict = field(default_factory=dict)
    handoffs: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "instructions": self.instructions,
            "memories": [
                {"name": m["name"], "scope": m["scope"], "kind": m.get("kind")}
                for m in self.memories
            ],
            "skills": [
                {"name": s["name"], "scope": s["scope"], "generation": s["generation"], "origin": s.get("origin")}
                for s in self.skills
            ],
            "tool_families": self.tool_families,
            "workspace_focus": self.workspace_focus,
            "handoffs": [
                {
                    "session_id": item.get("session_id"),
                    "task_signature": item.get("task_signature"),
                    "execution_cwd": item.get("execution_cwd"),
                    "verification": item.get("verification"),
                }
                for item in self.handoffs
            ],
        }


class ContextBuilder:
    def __init__(
        self,
        workspace_root: Path,
        execution_root: Path,
        instruction_candidates: list[Path],
        skill_retriever: SkillRetriever,
        memory_retriever: MemoryRetriever,
        session_store: SessionStore | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.execution_root = execution_root.resolve()
        self.instruction_candidates = instruction_candidates
        self.skill_retriever = skill_retriever
        self.memory_retriever = memory_retriever
        self.session_store = session_store

    def _workspace_focus(self) -> dict[str, str]:
        execution_cwd = "."
        if self.execution_root != self.workspace_root:
            execution_cwd = str(self.execution_root.relative_to(self.workspace_root))
        return {
            "workspace_root": str(self.workspace_root),
            "execution_cwd": execution_cwd,
            "text": (
                f"Workspace root: {self.workspace_root}. "
                f"Active execution directory: {execution_cwd}. "
                f"Prefer commands and new files relative to {execution_cwd} unless the prompt gives an exact repo path."
            ),
        }

    def build(
        self,
        prompt: str,
        task_signature: str,
        tool_families: list[str],
        *,
        current_session_id: str | None = None,
    ) -> ContextPackage:
        instructions: list[dict] = []
        for path in self.instruction_candidates:
            if path.exists():
                instructions.append({"path": str(path), "text": read_text(path)[:6000]})
        memories: list[dict] = []
        brief = self.memory_retriever.project_brief()
        if brief is not None:
            memories.append(
                {
                    "id": brief.id,
                    "name": brief.name,
                    "title": brief.title,
                    "scope": brief.scope,
                    "kind": brief.kind,
                    "path": str(brief.path),
                    "text": brief.text[:3000],
                }
            )
        seen_ids = {item["id"] for item in memories}
        for note in self.memory_retriever.retrieve(prompt):
            if note.id in seen_ids:
                continue
            memories.append(
                {
                    "id": note.id,
                    "name": note.name,
                    "title": note.title,
                    "scope": note.scope,
                    "kind": note.kind,
                    "path": str(note.path),
                    "text": note.text[:3000],
                }
            )
            seen_ids.add(note.id)
        skills = [
            {
                "name": skill.name,
                "scope": skill.scope,
                "origin": skill.origin,
                "generation": skill.generation,
                "path": str(skill.path),
                "description": skill.description,
                "text": skill.body[:6000],
            }
            for skill in self.skill_retriever.retrieve(prompt, task_signature)
        ]
        workspace_focus = self._workspace_focus()
        handoffs: list[dict] = []
        if self.session_store is not None:
            handoffs = [
                {
                    "session_id": item.get("session_id"),
                    "session_title": item.get("session_title"),
                    "task_signature": item.get("task_signature"),
                    "verification": item.get("verification"),
                    "execution_cwd": item.get("execution_cwd"),
                    "text": str(item.get("text") or "")[:1500],
                }
                for item in self.session_store.retrieve_handoffs(
                    prompt,
                    execution_cwd=workspace_focus["execution_cwd"],
                    limit=3,
                    exclude_session_id=current_session_id,
                )
            ]
        return ContextPackage(
            instructions=instructions,
            memories=memories,
            skills=skills,
            tool_families=tool_families,
            workspace_focus=workspace_focus,
            handoffs=handoffs,
        )
