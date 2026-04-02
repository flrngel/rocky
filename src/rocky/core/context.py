from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rocky.memory.retriever import MemoryRetriever
from rocky.skills.retriever import SkillRetriever
from rocky.util.io import read_text


@dataclass(slots=True)
class ContextPackage:
    instructions: list[dict]
    memories: list[dict]
    skills: list[dict]
    tool_families: list[str]

    def summary(self) -> dict:
        return {
            'instructions': self.instructions,
            'memories': [{'name': m['name'], 'scope': m['scope'], 'kind': m.get('kind')} for m in self.memories],
            'skills': [{'name': s['name'], 'scope': s['scope'], 'generation': s['generation']} for s in self.skills],
            'tool_families': self.tool_families,
        }


class ContextBuilder:
    def __init__(
        self,
        workspace_root: Path,
        instruction_candidates: list[Path],
        skill_retriever: SkillRetriever,
        memory_retriever: MemoryRetriever,
    ) -> None:
        self.workspace_root = workspace_root
        self.instruction_candidates = instruction_candidates
        self.skill_retriever = skill_retriever
        self.memory_retriever = memory_retriever

    def build(self, prompt: str, task_signature: str, tool_families: list[str]) -> ContextPackage:
        instructions: list[dict] = []
        for path in self.instruction_candidates:
            if path.exists():
                instructions.append({'path': str(path), 'text': read_text(path)[:6000]})
        memories: list[dict] = []
        brief = self.memory_retriever.project_brief()
        if brief is not None:
            memories.append(
                {
                    'id': brief.id,
                    'name': brief.name,
                    'title': brief.title,
                    'scope': brief.scope,
                    'kind': brief.kind,
                    'path': str(brief.path),
                    'text': brief.text[:3000],
                }
            )
        seen_ids = {item['id'] for item in memories}
        for note in self.memory_retriever.retrieve(prompt):
            if note.id in seen_ids:
                continue
            memories.append(
                {
                    'id': note.id,
                    'name': note.name,
                    'title': note.title,
                    'scope': note.scope,
                    'kind': note.kind,
                    'path': str(note.path),
                    'text': note.text[:3000],
                }
            )
            seen_ids.add(note.id)
        skills = [
            {
                'name': skill.name,
                'scope': skill.scope,
                'generation': skill.generation,
                'path': str(skill.path),
                'description': skill.description,
                'text': skill.body[:6000],
            }
            for skill in self.skill_retriever.retrieve(prompt, task_signature)
        ]
        return ContextPackage(
            instructions=instructions,
            memories=memories,
            skills=skills,
            tool_families=tool_families,
        )
