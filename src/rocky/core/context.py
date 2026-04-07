from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rocky.core.runtime_state import ActiveTaskThread, AnswerContract, EvidenceGraph
from rocky.memory.retriever import MemoryRetriever
from rocky.session.store import SessionStore
from rocky.skills.retriever import SkillRetriever
from rocky.student.store import StudentStore
from rocky.util.io import read_text


@dataclass(slots=True)
class ContextPackage:
    instructions: list[dict]
    memories: list[dict]
    skills: list[dict]
    tool_families: list[str]
    workspace_focus: dict = field(default_factory=dict)
    handoffs: list[dict] = field(default_factory=list)
    thread_summary: dict[str, Any] = field(default_factory=dict)
    evidence_summary: dict[str, Any] = field(default_factory=dict)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    answer_target: dict[str, Any] = field(default_factory=dict)
    student_profile: dict[str, Any] = field(default_factory=dict)
    student_notes: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "instructions": self.instructions,
            "memories": [
                {
                    "name": m["name"],
                    "scope": m["scope"],
                    "kind": m.get("kind"),
                    "provenance_type": m.get("provenance_type"),
                    "contradiction_state": m.get("contradiction_state"),
                }
                for m in self.memories
            ],
            "skills": [
                {
                    "name": s["name"],
                    "scope": s["scope"],
                    "generation": s["generation"],
                    "origin": s.get("origin"),
                    "promotion_state": s.get("promotion_state"),
                    "failure_class": s.get("failure_class"),
                }
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
                    "thread_id": item.get("thread_id"),
                }
                for item in self.handoffs
            ],
            "thread_summary": self.thread_summary,
            "evidence_summary": self.evidence_summary,
            "contradictions": self.contradictions,
            "answer_target": self.answer_target,
            "student_profile": self.student_profile,
            "student_notes": [
                {
                    "id": item.get("id"),
                    "kind": item.get("kind"),
                    "title": item.get("title"),
                    "task_signature": item.get("task_signature"),
                    "thread_id": item.get("thread_id"),
                }
                for item in self.student_notes
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
        student_store: StudentStore | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.execution_root = execution_root.resolve()
        self.instruction_candidates = instruction_candidates
        self.skill_retriever = skill_retriever
        self.memory_retriever = memory_retriever
        self.session_store = session_store
        self.student_store = student_store

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

    def _thread_summary(self, thread: ActiveTaskThread | None) -> dict[str, Any]:
        if thread is None:
            return {}
        latest_prompt = thread.prompt_history[-1]["prompt"] if thread.prompt_history else ""
        latest_answer = thread.answer_history[-1]["answer"] if thread.answer_history else ""
        recent_tools = [
            event.get("name")
            for event in thread.tool_history[-8:]
            if isinstance(event, dict) and event.get("type") == "tool_result" and event.get("name")
        ]
        return {
            "thread_id": thread.thread_id,
            "task_family": thread.task_family,
            "task_signature": thread.task_signature,
            "status": thread.status,
            "execution_cwd": thread.execution_cwd,
            "artifacts": thread.artifact_refs[:8],
            "entities": thread.entity_refs[:8],
            "unresolved_questions": thread.unresolved_questions[:6],
            "latest_prompt": latest_prompt[:500],
            "latest_answer": latest_answer[:500],
            "recent_tools": recent_tools,
            "text": thread.summary_text(),
        }

    def _evidence_summary(self, evidence_graph: EvidenceGraph | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if evidence_graph is None:
            return {}, []
        supported = evidence_graph.supported_claims(include_statuses={"active", "provisional"})
        contradictions: list[dict[str, Any]] = []
        for claim in supported:
            if claim.contradiction_refs:
                contradictions.append(
                    {
                        "claim_id": claim.claim_id,
                        "text": claim.text,
                        "contradiction_refs": claim.contradiction_refs[:6],
                        "status": claim.status,
                    }
                )
        summary = {
            "thread_id": evidence_graph.thread_id,
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "text": claim.text,
                    "provenance_type": claim.provenance_type,
                    "provenance_source": claim.provenance_source,
                    "confidence": claim.confidence,
                    "status": claim.status,
                }
                for claim in supported[:12]
            ],
            "artifacts": evidence_graph.artifacts[:12],
            "entities": evidence_graph.entities[:12],
            "questions": evidence_graph.questions[:8],
            "decisions": evidence_graph.decisions[:8],
            "corrections": evidence_graph.corrections[:8],
        }
        return summary, contradictions[:8]

    def _student_context(
        self,
        prompt: str,
        task_signature: str,
        thread: ActiveTaskThread | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.student_store is None:
            return {}, []
        profile = self.student_store.profile()
        notes = self.student_store.retrieve(prompt, task_signature=task_signature, thread=thread, limit=5)
        return profile, notes

    def build(
        self,
        prompt: str,
        task_signature: str,
        tool_families: list[str],
        *,
        current_session_id: str | None = None,
        active_thread: ActiveTaskThread | None = None,
        evidence_graph: EvidenceGraph | None = None,
        answer_contract: AnswerContract | None = None,
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
                    "provenance_type": getattr(brief, "provenance_type", "learned_rule"),
                    "contradiction_state": getattr(brief, "contradiction_state", "active"),
                }
            )
        seen_ids = {item["id"] for item in memories}
        for note in self.memory_retriever.retrieve(
            prompt,
            task_signature=task_signature,
            thread=active_thread,
        ):
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
                    "provenance_type": getattr(note, "provenance_type", "user_asserted"),
                    "contradiction_state": getattr(note, "contradiction_state", "active"),
                    "supporting_claim_ids": getattr(note, "supporting_claim_ids", []),
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
                "promotion_state": skill.metadata.get("promotion_state", "promoted"),
                "failure_class": skill.metadata.get("failure_class"),
                "task_family": skill.metadata.get("task_family"),
            }
            for skill in self.skill_retriever.retrieve(prompt, task_signature, thread=active_thread)
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
                    "thread_id": item.get("thread_id"),
                    "text": str(item.get("text") or "")[:1500],
                }
                for item in self.session_store.retrieve_handoffs(
                    prompt,
                    execution_cwd=workspace_focus["execution_cwd"],
                    limit=3,
                    exclude_session_id=current_session_id,
                )
            ]
        thread_summary = self._thread_summary(active_thread)
        evidence_summary, contradictions = self._evidence_summary(evidence_graph)
        answer_target = answer_contract.as_record() if answer_contract is not None else {}
        student_profile, student_notes = self._student_context(prompt, task_signature, active_thread)
        return ContextPackage(
            instructions=instructions,
            memories=memories,
            skills=skills,
            tool_families=tool_families,
            workspace_focus=workspace_focus,
            handoffs=handoffs,
            thread_summary=thread_summary,
            evidence_summary=evidence_summary,
            contradictions=contradictions,
            answer_target=answer_target,
            student_profile=student_profile,
            student_notes=student_notes,
        )
