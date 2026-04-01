from __future__ import annotations

from rocky.skills.models import Skill
from rocky.util.text import tokenize_keywords

WEAK_MATCH_TOKENS = {"command", "find", "help", "information", "task", "user"}


class SkillRetriever:
    def __init__(self, skills: list[Skill]) -> None:
        self.skills = skills

    def inventory(self) -> list[dict]:
        return [skill.as_record() for skill in self.skills]

    def retrieve(self, prompt: str, task_signature: str, limit: int = 4) -> list[Skill]:
        prompt_lower = prompt.lower()
        query_words = tokenize_keywords(prompt)
        scored: list[tuple[int, Skill]] = []
        for skill in self.skills:
            score = 0
            trigger_match = any(trigger.lower() in prompt_lower for trigger in skill.triggers)
            name_tokens = tokenize_keywords(skill.name)
            description_tokens = tokenize_keywords(skill.description)
            trigger_tokens = set().union(*(tokenize_keywords(trigger) for trigger in skill.triggers))
            token_matches = (
                (query_words & name_tokens)
                | (query_words & description_tokens)
                | (query_words & trigger_tokens)
            )
            strong_token_matches = token_matches - WEAK_MATCH_TOKENS
            token_overlap = (
                len(query_words & name_tokens) * 3
                + len(query_words & description_tokens)
                + len(query_words & trigger_tokens) * 2
            )
            score += token_overlap
            if trigger_match:
                score += 6
            task_signature_score = 0
            task_signature_score += sum(3 for sig in skill.task_signatures if sig.endswith('*') and task_signature.startswith(sig[:-1]))
            task_signature_score += sum(5 for sig in skill.task_signatures if sig == task_signature)
            score += task_signature_score
            if skill.scope == 'project':
                score += 1
            if not trigger_match and not task_signature_score and not strong_token_matches:
                continue
            if score < 2 and not trigger_match:
                continue
            if score:
                scored.append((score, skill))
        scored.sort(key=lambda item: (item[0], item[1].generation, item[1].scope == 'project'), reverse=True)
        return [skill for _, skill in scored[:limit]]
