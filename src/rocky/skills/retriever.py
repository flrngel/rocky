from __future__ import annotations

from rocky.core.runtime_state import ActiveTaskThread
from rocky.skills.models import Skill
from rocky.util.text import tokenize_keywords

WEAK_MATCH_TOKENS = {"command", "find", "help", "information", "task", "user"}


class SkillRetriever:
    def __init__(self, skills: list[Skill]) -> None:
        self.skills = skills

    def inventory(self) -> list[dict]:
        return [skill.as_record() for skill in self.skills]

    def retrieve(
        self,
        prompt: str,
        task_signature: str,
        *,
        thread: ActiveTaskThread | None = None,
        limit: int = 4,
    ) -> list[Skill]:
        prompt_lower = prompt.lower()
        query_words = tokenize_keywords(prompt)
        thread_words = tokenize_keywords(thread.summary_text()) if thread is not None else set()
        scored: list[tuple[tuple[int, int, int], Skill]] = []
        for skill in self.skills:
            score = 0
            trigger_match = any(trigger.lower() in prompt_lower for trigger in skill.triggers)
            name_tokens = tokenize_keywords(skill.name)
            description_tokens = tokenize_keywords(skill.description)
            trigger_tokens = set().union(*(tokenize_keywords(trigger) for trigger in skill.triggers))
            keyword_tokens = set().union(*(tokenize_keywords(keyword) for keyword in skill.retrieval_keywords))
            token_matches = (
                (query_words & name_tokens)
                | (query_words & description_tokens)
                | (query_words & trigger_tokens)
                | (query_words & keyword_tokens)
                | (thread_words & keyword_tokens)
            )
            strong_token_matches = token_matches - WEAK_MATCH_TOKENS
            token_overlap = (
                len(query_words & name_tokens) * 3
                + len(query_words & description_tokens)
                + len(query_words & trigger_tokens) * 2
                + len(query_words & keyword_tokens) * 2
                + len(thread_words & keyword_tokens)
            )
            score += token_overlap
            if trigger_match:
                score += 6
            task_signature_score = 0
            task_signature_score += sum(3 for sig in skill.task_signatures if sig.endswith('*') and task_signature.startswith(sig[:-1]))
            task_signature_score += sum(6 for sig in skill.task_signatures if sig == task_signature)
            score += task_signature_score
            if skill.scope == 'project':
                score += 2
            if skill.generation:
                score += min(skill.generation, 3)
            if not trigger_match and not task_signature_score and not strong_token_matches:
                continue
            if score < 2 and not trigger_match:
                continue
            scored.append(((score, skill.generation, 1 if skill.scope == 'project' else 0), skill))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [skill for _, skill in scored[:limit]]
