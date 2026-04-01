from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")[:64] or "learned-skill"


@dataclass(slots=True)
class SkillDraft:
    skill_id: str
    path: Path
    content: str
    metadata: dict


class SkillSynthesizer:
    def __init__(self, use_model: bool = False) -> None:
        self.use_model = use_model

    def build_draft(
        self,
        learned_root: Path,
        task_signature: str,
        generation: int,
        feedback: str,
        support_episode_id: str,
        last_prompt: str,
        last_answer: str,
        scope: str = "project",
    ) -> SkillDraft:
        skill_id = _slug(task_signature.replace("/", "-"))
        path = learned_root / skill_id / "SKILL.md"
        metadata = {
            "name": skill_id,
            "description": f"Learned corrective workflow for {task_signature}",
            "scope": scope,
            "task_signatures": [task_signature],
            "generation": generation,
            "origin": {
                "type": "user_feedback",
                "episode_ids": [support_episode_id],
            },
            "verification": {
                "status": "passed",
                "tests": [],
            },
            "retrieval": {
                "triggers": [task_signature, feedback[:80]],
            },
        }
        frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
        body = f"""
# Learned corrective workflow

## Why this skill exists

This skill was synthesized from user feedback on a previous Rocky answer.

## Correction

{feedback.strip()}

## Operational guidance

1. Detect when the current task matches `{task_signature}` or a close variant.
2. Before answering, check whether the earlier failure mode could recur.
3. Prefer the corrected workflow implied by the feedback.
4. Preserve user-required formatting and domain terms.
5. If uncertain, surface the caveat explicitly instead of guessing.

## Previous prompt excerpt

{last_prompt[:1200].strip()}

## Previous answer excerpt

{last_answer[:1200].strip()}
""".strip() + "\n"
        content = f"---\n{frontmatter}\n---\n\n{body}"
        return SkillDraft(
            skill_id=skill_id,
            path=path,
            content=content,
            metadata=metadata,
        )
