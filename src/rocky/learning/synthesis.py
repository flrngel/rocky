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


@dataclass(slots=True)
class FeedbackAnalysis:
    failure_class: str
    task_signature: str
    task_family: str
    title: str
    summary: str
    required_behavior: list[str]
    prohibited_behavior: list[str]
    evidence_requirements: list[str]
    triggers: list[str]
    keywords: list[str]
    path_hints: list[str]
    tool_names: list[str]
    prompt_excerpt: str
    answer_excerpt: str
    feedback_excerpt: str

    def pattern_text(self) -> str:
        required_text = "\n".join(f"- {item}" for item in self.required_behavior) or "- none captured"
        prohibited_text = "\n".join(f"- {item}" for item in self.prohibited_behavior) or "- none captured"
        evidence_text = "\n".join(f"- {item}" for item in self.evidence_requirements) or "- none captured"
        apply_text = "\n".join(
            [
                f"- Task signature: `{self.task_signature}`",
                f"- Task family: `{self.task_family}`",
                f"- Failure class: `{self.failure_class}`",
            ]
        )
        path_text = "\n".join(f"- `{item}`" for item in self.path_hints[:6]) or "- none captured"
        tool_text = "\n".join(f"- `{item}`" for item in self.tool_names[:6]) or "- none captured"
        return (
            "# Learned correction pattern\n\n"
            "## Summary\n\n"
            f"{self.summary}\n\n"
            "## Applies when\n\n"
            f"{apply_text}\n\n"
            "## Do this\n\n"
            f"{required_text}\n\n"
            "## Avoid this\n\n"
            f"{prohibited_text}\n\n"
            "## Evidence to gather\n\n"
            f"{evidence_text}\n\n"
            "## Relevant tools\n\n"
            f"{tool_text}\n\n"
            "## Workspace hints\n\n"
            f"{path_text}\n\n"
            "## Teacher feedback\n\n"
            f"{self.feedback_excerpt}\n\n"
            "## Prior prompt excerpt\n\n"
            f"{self.prompt_excerpt}\n\n"
            "## Prior answer excerpt\n\n"
            f"{self.answer_excerpt}\n"
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "task_signature": self.task_signature,
            "task_family": self.task_family,
            "title": self.title,
            "summary": self.summary,
            "required_behavior": list(self.required_behavior),
            "prohibited_behavior": list(self.prohibited_behavior),
            "evidence_requirements": list(self.evidence_requirements),
            "triggers": list(self.triggers),
            "keywords": list(self.keywords),
            "path_hints": list(self.path_hints),
            "tool_names": list(self.tool_names),
        }


class SkillSynthesizer:
    def __init__(self, *, use_model: bool = False) -> None:
        self.use_model = use_model

    @staticmethod
    def _mentions_expression_variants(lowered: str) -> bool:
        return any(term in lowered for term in ("cask strength", "single barrel", "barrel proof", "finish", "expression", "variant"))

    @staticmethod
    def _mentions_clear_base_family(lowered: str) -> bool:
        return any(
            term in lowered
            for term in (
                "empty array",
                "collapse to []",
                "confirmed matches",
                "matching 15-year",
                "matching products",
                "clear matching family",
                "base expression",
                "base query",
                "non-cask-strength",
                "over-pruned",
            )
        )

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
        if self._mentions_expression_variants(lowered) and any(
            term in lowered
            for term in (
                "different product",
                "different expression",
                "distinct release",
                "do not include",
                "don't include",
                "exclude",
                "variant",
            )
        ):
            return "product_expression_variant_misclassified"
        if any(term in lowered for term in ("continue", "follow-up", "resume", "continuation")):
            return "continuation_lost_after_tool_backed_work"
        if any(term in lowered for term in ("memory", "poison", "stored", "saved", "save")) and any(
            term in lowered for term in ("unsupported", "made up", "hallucinated", "fabricated", "guess", "guesses")
        ):
            return "project_memory_promotion_from_unsupported_inference"
        if any(term in lowered for term in ("unsupported", "made up", "hallucinated", "fabricated")):
            return "unsupported_claim_introduced"
        if any(term in lowered for term in ("repeated", "recap", "too much context", "replayed")):
            return "answer_recapped_previous_context"
        if any(term in lowered for term in ("memory", "poison", "stored", "saved", "save")):
            return "project_memory_promotion_from_unsupported_inference"
        if any(term in lowered for term in ("verify", "checked", "too early", "stopped")):
            return "tool_loop_ended_before_evidence_sufficiency"
        return str((trace or {}).get("verification", {}).get("failure_class") or "workflow_correction")

    def _required_behavior(self, task_signature: str, feedback: str, trace: dict[str, Any] | None = None) -> list[str]:
        lowered = feedback.lower()
        tool_names = [str(name) for name in ((trace or {}).get("selected_tools") or []) if str(name)]
        items: list[str] = []
        expression_variants = self._mentions_expression_variants(lowered)
        clear_base_family = self._mentions_clear_base_family(lowered)
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
        if any(term in lowered for term in ("json", "json array", "valid json", "stdout")):
            items.append("Return the final deliverable as valid JSON only with no markdown fences, prose, or malformed keys.")
        if expression_variants:
            items.append(
                "When matching product duplicates, treat cask strength, single barrel, barrel proof, and distinct cask finishes as separate expressions unless the query explicitly names that variant."
            )
        if expression_variants and clear_base_family:
            items.append(
                "When observed results show one clear base-expression family for the requested product, keep that family's naming variants as matches and exclude other expression families from the final candidate list."
            )
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
        expression_variants = self._mentions_expression_variants(lowered)
        clear_base_family = self._mentions_clear_base_family(lowered)
        if any(term in lowered for term in ("json", "json array", "valid json")):
            items.append("Do not wrap final JSON in markdown fences or leave malformed JSON syntax in the answer.")
        if any(term in lowered for term in ("continue", "resume", "follow-up")):
            items.append("Do not drop into generic chat routing when the user is continuing an active artifact-backed task.")
        if any(term in lowered for term in ("memory", "store", "save", "poison")):
            items.append("Do not promote answer rhetoric or one-off speculation into durable project memory.")
        if expression_variants:
            items.append("Do not include distinct expression variants as candidates for the base product just because distillery and age match.")
        if expression_variants and clear_base_family:
            items.append("Do not return an empty result or keep other expression families as fallback uncertainty once the clear base-expression family is present in the observed results.")
        if any(term in lowered for term in ("exact", "verified", "check")):
            items.append("Do not claim verification until the relevant tool evidence has been gathered in this run.")
        return items[:8]

    def _evidence_requirements(self, task_signature: str) -> list[str]:
        items = [
            "Map final answer claims to tool-observed facts, explicit user assertions, or explicit uncertainty.",
            "Prefer current-thread evidence over stale summaries or prior answer narration.",
        ]
        if task_signature == "repo/shell_execution":
            items.append("Treat current command output from this run as the source of truth.")
        return items[:8]

    def _analysis_title(self, task_signature: str, failure_class: str) -> str:
        family = task_signature.replace("/", " ").strip() or "workflow"
        failure = failure_class.replace("_", " ").strip()
        return f"{family}: {failure}"

    def analyze_feedback(
        self,
        task_signature: str,
        feedback: str,
        last_prompt: str,
        last_answer: str,
        trace: dict[str, Any] | None = None,
        *,
        task_family: str | None = None,
        thread_id: str | None = None,
        failure_class: str | None = None,
    ) -> FeedbackAnalysis:
        trace = trace or {}
        resolved_task_family = task_family or task_signature.split("/", 1)[0]
        tool_names = [str(name) for name in (trace.get("selected_tools") or []) if str(name)]
        feedback_keywords = sorted(tokenize_keywords(feedback))
        prompt_keywords = sorted(tokenize_keywords(last_prompt))
        path_hints = self._path_hints(last_prompt, last_answer, feedback, json.dumps(trace, ensure_ascii=False))
        resolved_failure_class = failure_class or self._failure_class(feedback, trace)
        required_behavior = self._required_behavior(task_signature, feedback, trace)
        prohibited_behavior = self._prohibited_behavior(feedback)
        evidence_requirements = self._evidence_requirements(task_signature)
        triggers: list[str] = []
        for item in [
            task_signature,
            resolved_task_family,
            resolved_failure_class,
            *path_hints,
            *tool_names[:4],
            *prompt_keywords[:6],
            *feedback_keywords[:8],
            *(thread_id and [thread_id] or []),
        ]:
            if item and item not in triggers:
                triggers.append(item)
        summary = feedback.strip().splitlines()[0][:200] if feedback.strip() else f"Correction for {task_signature}"
        title = self._analysis_title(task_signature, resolved_failure_class)
        return FeedbackAnalysis(
            failure_class=resolved_failure_class,
            task_signature=task_signature,
            task_family=resolved_task_family,
            title=title,
            summary=summary,
            required_behavior=required_behavior,
            prohibited_behavior=prohibited_behavior,
            evidence_requirements=evidence_requirements,
            triggers=triggers[:16],
            keywords=feedback_keywords[:16],
            path_hints=path_hints[:8],
            tool_names=tool_names[:8],
            prompt_excerpt=last_prompt[:1200].strip(),
            answer_excerpt=last_answer[:1200].strip(),
            feedback_excerpt=feedback.strip()[:2000],
        )

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
        analysis = self.analyze_feedback(
            task_signature,
            feedback,
            last_prompt,
            last_answer,
            trace,
            task_family=task_family,
            thread_id=thread_id,
            failure_class=failure_class,
        )
        skill_id = _slug(analysis.failure_class + "-" + task_signature.replace("/", "-"))
        path = learned_root / skill_id / "SKILL.md"
        metadata = {
            "name": skill_id,
            "description": feedback.strip().splitlines()[0][:140] if feedback.strip() else f"Learned workflow correction for {task_signature}",
            "scope": scope,
            "task_signatures": [task_signature],
            "task_family": analysis.task_family,
            "generation": generation,
            "origin": {
                "type": "user_feedback",
                "episode_ids": [support_episode_id],
                "thread_id": thread_id,
            },
            "failure_class": analysis.failure_class,
            "promotion_state": "candidate",
            "reuse_count": 0,
            "verified_success_count": 0,
            "verification": {
                "status": "candidate",
                "tests": [],
            },
            "retrieval": {
                "triggers": analysis.triggers[:12],
                "keywords": analysis.keywords[:12],
            },
            "tools": analysis.tool_names[:8],
            "paths": analysis.path_hints[:8],
            "required_behavior": analysis.required_behavior,
            "prohibited_behavior": analysis.prohibited_behavior,
            "evidence_requirements": analysis.evidence_requirements,
        }
        frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
        required_text = "\n".join(f"- {item}" for item in analysis.required_behavior) or "- none captured"
        prohibited_text = "\n".join(f"- {item}" for item in analysis.prohibited_behavior) or "- none captured"
        evidence_text = "\n".join(f"- {item}" for item in analysis.evidence_requirements) or "- none captured"
        path_text = "\n".join(f"- `{path_hint}`" for path_hint in analysis.path_hints[:6]) or "- none captured"
        body = f"""
# Learned corrective workflow

## Why this skill exists

This skill was synthesized from user feedback on a previous Rocky answer.

## Failure class

{analysis.failure_class}

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

{analysis.prompt_excerpt}

## Previous answer excerpt

{analysis.answer_excerpt}
""".strip() + "\n"
        content = f"---\n{frontmatter}\n---\n\n{body}"
        return SkillDraft(skill_id=skill_id, path=path, content=content, metadata=metadata)
