from __future__ import annotations

import re

from rocky.skills.models import Skill


class SkillRetriever:
    def __init__(self, skills: list[Skill]) -> None:
        self.skills = skills

    def inventory(self) -> list[dict]:
        return [skill.as_record() for skill in self.skills]

    def retrieve(self, prompt: str, task_signature: str, limit: int = 4) -> list[Skill]:
        query_words = {w for w in re.findall(r'[a-zA-Z0-9_\-]+', prompt.lower()) if len(w) > 2}
        scored: list[tuple[int, Skill]] = []
        for skill in self.skills:
            score = 0
            haystack = ' '.join([skill.name, skill.description, *skill.triggers, *skill.task_signatures]).lower()
            score += sum(2 for word in query_words if word in haystack)
            score += sum(3 for sig in skill.task_signatures if sig.endswith('*') and task_signature.startswith(sig[:-1]))
            score += sum(4 for sig in skill.task_signatures if sig == task_signature)
            if skill.scope == 'project':
                score += 1
            if score:
                scored.append((score, skill))
        scored.sort(key=lambda item: (item[0], item[1].generation, item[1].scope == 'project'), reverse=True)
        return [skill for _, skill in scored[:limit]]
