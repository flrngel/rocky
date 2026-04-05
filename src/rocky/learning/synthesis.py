from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

import yaml

from rocky.util.text import tokenize_keywords


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:72] or "skill"


@dataclass(slots=True)
class SkillDraft:
    skill_id: str
    path: Path
    content: str
    metadata: dict[str, Any]


class SkillSynthesizer:
    def __init__(self, *, use_model: bool = False) -> None:
        self.use_model = use_model

    def _path_hints(self, *texts: str) -> list[str]:
        path_re = re.compile(r"(?<![A-Za-z0-9])(?:\.{1,2}/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
        seen: set[str] = set()
        ordered: list[str] = []
        for text in texts:
            for match in path_re.findall(text or ""):
                candidate = match.strip(".,:;()[]{}<>`\"'")
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                ordered.append(candidate)
        return ordered

    def _failure_class(self, feedback: str, trace: dict[str, Any] | None = None) -> str:
        lowered = feedback.lower()
        if any(term in lowered for term in ("continue", "follow-up", "resume", "continuation")):
            return "continuation_lost_after_tool_backed_work"
        if any(term in lowered for term in ("unsupported", "made up", "hallucinated", "fabricated")):
            return "unsupported_claim_introduced"
        if any(term in lowered for term in ("repeated", "recap", "too much context", "replayed")):
            return "answer_recapped_previous_context"
        if any(term in lowered for term in ("memory", "poison", "stored", "saved")):
            return "project_memory_promotion_from_unsupported_inference"
        if any(term in lowered for term in ("verify", "checked", "too early", "stopped")):
            return "tool_loop_ended_before_evidence_sufficiency"
        return str((trace or {}).get("verification", {}).get("failure_class") or "workflow_correction")

    def _required_behavior(self, task_signature: str, feedback: str, trace: dict[str, Any] | None = None) -> list[str]:
        lowered = feedback.lower()
        tool_names = [str(name) for name in ((trace or {}).get("selected_tools") or []) if str(name)]
        items: list[str] = []
        if task_signature.startswith("repo/"):
            items.append("Stay inside the active repo/task thread across short follow-up turns.")
            items.append("Ground file, shell, and git claims in fresh tool evidence from this run.")
        if task_signature == "repo/shell_execution":
            items.append("Run the requested command or workspace script first, then inspect the produced output or artifact before deciding.")
        if task_signature == "local/runtime_inspection":
            items.append("Start with `inspect_runtime_versions`, then confirm with at least one shell command.")
        if task_signature == "automation/general":
            items.append("Create or edit the script with `write_file`, reread it, then verify with `run_shell_command`.")
        if task_signature == "extract/general":
            items.append("Use a locate-or-read step before parsing, then return valid JSON only.")
        if any(term in lowered for term in ("exact json", "json file", "reread", "read back")):
            items.append("If the user asks for a result file or exact JSON, write the exact payload and reread it before answering.")
        if not items and tool_names:
            items.append("Prefer the evidence-producing tool flow that previously proved useful here: " + ", ".join(tool_names[:4]) + ".")
        if not items:
            items.append("Prefer evidence-producing steps before final narration.")
        return items[:8]

    def _prohibited_behavior(self, feedback: str) -> list[str]:
        lowered = feedback.lower()
        items: list[str] = [
            "Do not turn unsupported inference into deterministic truth.",
            "Do not answer the current ask by replaying prior setup unless it is required for correctness.",
        ]
        if any(term in lowered for term in ("continue", "resume", "follow-up")):
            items.append("Do not drop into generic chat routing when the user is continuing an active artifact-backed task.")
        if any(term in lowered for term in ("memory", "store", "save", "poison")):
            items.append("Do not promote answer rhetoric or one-off speculation into durable project memory.")
        if any(term in lowered for term in ("exact", "verified", "check")):
            items.append("Do not claim verification until the relevant tool evidence has been gathered in this run.")
        return items[:8]

    def build_draft(
        self,
        learned_root: Path,
        task_signature: str,
        generation: int,
        feedback: str,
        support_episode_id: str,
        last_prompt: str,
        last_answer: str,
        trace: dict[str, Any] | None = None,
        scope: str = "project",
        *,
        task_family: str | None = None,
        thread_id: str | None = None,
        failure_class: str | None = None,
    ) -> SkillDraft:
        skill_id = _slug((failure_class or self._failure_class(feedback, trace)) + "-" + task_signature.replace("/", "-"))
        path = learned_root / skill_id / "SKILL.md"
        trace = trace or {}
        tool_names = [str(name) for name in (trace.get("selected_tools") or []) if str(name)]
        prompt_keywords = sorted(tokenize_keywords(last_prompt))
        feedback_keywords = sorted(tokenize_keywords(feedback))
        path_hints = self._path_hints(last_prompt, last_answer, feedback, json.dumps(trace, ensure_ascii=False))
        resolved_failure_class = failure_class or self._failure_class(feedback, trace)
        triggers = []
        for item in [task_signature, *(task_family and [task_family] or []), resolved_failure_class, *path_hints, *tool_names[:4], *prompt_keywords[:6], *feedback_keywords[:6]]:
            if item and item not in triggers:
                triggers.append(item)
        required_behavior = self._required_behavior(task_signature, feedback, trace)
        prohibited_behavior = self._prohibited_behavior(feedback)
        evidence_requirements = [
            "Map final answer claims to tool-observed facts, explicit user assertions, or explicit uncertainty.",
            "Prefer current-thread evidence over stale summaries or prior answer narration.",
        ]
        if task_signature == "repo/shell_execution":
            evidence_requirements.append("Treat current command output from this run as the source of truth.")
        metadata = {
            "name": skill_id,
            "description": feedback.strip().splitlines()[0][:140] if feedback.strip() else f"Learned workflow correction for {task_signature}",
            "scope": scope,
            "task_signatures": [task_signature],
            "task_family": task_family or task_signature.split("/", 1)[0],
            "generation": generation,
            "origin": {
                "type": "user_feedback",
                "episode_ids": [support_episode_id],
                "thread_id": thread_id,
            },
            "failure_class": resolved_failure_class,
            "promotion_state": "candidate",
            "reuse_count": 0,
            "verified_success_count": 0,
            "verification": {
                "status": "candidate",
                "tests": [],
            },
            "retrieval": {
                "triggers": triggers[:12],
                "keywords": feedback_keywords[:12],
            },
            "tools": tool_names[:8],
            "paths": path_hints[:8],
            "required_behavior": required_behavior,
            "prohibited_behavior": prohibited_behavior,
            "evidence_requirements": evidence_requirements,
        }
        frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
        required_text = "\n".join(f"- {item}" for item in required_behavior) or "- none captured"
        prohibited_text = "\n".join(f"- {item}" for item in prohibited_behavior) or "- none captured"
        evidence_text = "\n".join(f"- {item}" for item in evidence_requirements) or "- none captured"
        path_text = "\n".join(f"- `{path_hint}`" for path_hint in path_hints[:6]) or "- none captured"
        body = f"""
# Learned corrective workflow

## Why this skill exists

This skill was synthesized from user feedback on a previous Rocky answer.

## Failure class

{resolved_failure_class}

## Correction from the user

{feedback.strip()}

## Operational guidance

### Required behavior

{required_text}

### Prohibited behavior

{prohibited_text}

## Evidence requirements

{evidence_text}

## Workspace hints

{path_text}

## Previous prompt excerpt

{last_prompt[:1200].strip()}

## Previous answer excerpt

{last_answer[:1200].strip()}
""".strip() + "\n"
        content = f"---\n{frontmatter}\n---\n\n{body}"
        return SkillDraft(skill_id=skill_id, path=path, content=content, metadata=metadata)
