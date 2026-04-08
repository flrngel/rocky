from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

import yaml

from rocky.core.messages import Message
from rocky.util.text import extract_json_candidate, safe_json, tokenize_keywords


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
    root_cause: str = ""
    corrected_outcome: str = ""
    generalization_rationale: str = ""
    evidence: list[str] = field(default_factory=list)
    debug_steps: list[str] = field(default_factory=list)
    memory_kind: str = "pattern"
    should_publish_skill: bool = True
    reflection_source: str = "heuristic_fallback"
    confidence: float = 0.0

    def memory_text(self) -> str:
        heading = {
            "pattern": "# Learned correction pattern",
            "example": "# Learned correction example",
            "lesson": "# Learned correction lesson",
        }.get(self.memory_kind, "# Learned correction pattern")
        required_text = "\n".join(f"- {item}" for item in self.required_behavior) or "- none captured"
        prohibited_text = "\n".join(f"- {item}" for item in self.prohibited_behavior) or "- none captured"
        evidence_requirements_text = "\n".join(f"- {item}" for item in self.evidence_requirements) or "- none captured"
        evidence_text = "\n".join(f"- {item}" for item in self.evidence[:8]) or "- none captured"
        debug_text = "\n".join(f"- {item}" for item in self.debug_steps[:8]) or "- none captured"
        apply_text = "\n".join(
            [
                f"- Task signature: `{self.task_signature}`",
                f"- Task family: `{self.task_family}`",
                f"- Failure class: `{self.failure_class}`",
                f"- Reflection source: `{self.reflection_source}`",
                f"- Confidence: `{self.confidence:.2f}`",
            ]
        )
        path_text = "\n".join(f"- `{item}`" for item in self.path_hints[:6]) or "- none captured"
        tool_text = "\n".join(f"- `{item}`" for item in self.tool_names[:6]) or "- none captured"
        return (
            f"{heading}\n\n"
            "## Summary\n\n"
            f"{self.summary}\n\n"
            "## Root cause\n\n"
            f"{self.root_cause or self.summary}\n\n"
            "## Evidence observed\n\n"
            f"{evidence_text}\n\n"
            "## Why this should be remembered\n\n"
            f"{self.generalization_rationale or 'This feedback should guide future answers with the same failure shape.'}\n\n"
            "## Applies when\n\n"
            f"{apply_text}\n\n"
            "## Reflection flow\n\n"
            f"{debug_text}\n\n"
            "## Do this\n\n"
            f"{required_text}\n\n"
            "## Avoid this\n\n"
            f"{prohibited_text}\n\n"
            "## Evidence to gather\n\n"
            f"{evidence_requirements_text}\n\n"
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

    def pattern_text(self) -> str:
        return self.memory_text()

    def as_record(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "task_signature": self.task_signature,
            "task_family": self.task_family,
            "title": self.title,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "corrected_outcome": self.corrected_outcome,
            "generalization_rationale": self.generalization_rationale,
            "evidence": list(self.evidence),
            "debug_steps": list(self.debug_steps),
            "memory_kind": self.memory_kind,
            "should_publish_skill": self.should_publish_skill,
            "reflection_source": self.reflection_source,
            "confidence": self.confidence,
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
    def _truncate(value: Any, limit: int = 320) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    @staticmethod
    def _string_list(value: Any, *, limit: int = 8) -> list[str]:
        if isinstance(value, str):
            items = [value]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
        else:
            items = []
        ordered: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered[:limit]

    @staticmethod
    def _bool_value(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1"}:
                return True
            if lowered in {"false", "no", "0"}:
                return False
        return default

    @staticmethod
    def _float_value(value: Any, default: float = 0.0) -> float:
        try:
            numeric = float(value)
        except Exception:
            return default
        return min(max(numeric, 0.0), 1.0)

    @staticmethod
    def _memory_kind(value: Any, default: str = "pattern") -> str:
        lowered = str(value or "").strip().lower()
        if lowered in {"pattern", "example", "lesson"}:
            return lowered
        return default

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

    def _tool_result_summary(self, event: dict[str, Any]) -> str:
        name = str(event.get("name") or "tool_result")
        status = "success" if event.get("success", True) else "failure"
        raw_text = str(event.get("text") or "").strip()
        summary_parts: list[str] = []
        text_body = raw_text
        try:
            payload = json.loads(raw_text)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            summary = str(payload.get("summary") or "").strip()
            if summary:
                summary_parts.append(summary)
            data = payload.get("data") or {}
            if isinstance(data, dict):
                command = str(data.get("command") or "").strip()
                path = str(data.get("path") or "").strip()
                stdout = str(data.get("stdout") or "").strip()
                stderr = str(data.get("stderr") or "").strip()
                if command:
                    summary_parts.append(f"command={command}")
                if path:
                    summary_parts.append(f"path={path}")
                if stdout:
                    text_body = " | ".join(line.strip() for line in stdout.splitlines() if line.strip()) or stdout
                elif stderr:
                    text_body = " | ".join(line.strip() for line in stderr.splitlines() if line.strip()) or stderr
                else:
                    text_body = safe_json(data)
        if text_body:
            summary_parts.append(self._truncate(text_body.replace("\n", " | "), 360))
        return f"{name} [{status}]: " + "; ".join(part for part in summary_parts if part)

    def _trace_snapshot(self, trace: dict[str, Any]) -> dict[str, Any]:
        route = trace.get("route") or {}
        verification = trace.get("verification") or {}
        thread = (trace.get("thread") or {}).get("current_thread") or {}
        supported_claims = [
            self._truncate(claim.get("text", ""), 240)
            for claim in (trace.get("supported_claims") or [])[:8]
            if str(claim.get("text") or "").strip()
        ]
        tool_evidence = [
            self._tool_result_summary(event)
            for event in (trace.get("tool_events") or [])
            if event.get("type") == "tool_result"
        ]
        return {
            "route": {
                "task_signature": route.get("task_signature"),
                "task_class": route.get("task_class"),
                "source": route.get("source"),
                "reasoning": self._truncate(route.get("reasoning", ""), 240),
            },
            "verification": {
                "status": verification.get("status"),
                "message": self._truncate(verification.get("message", ""), 240),
                "failure_class": verification.get("failure_class"),
            },
            "thread": {
                "thread_id": thread.get("thread_id"),
                "task_signature": thread.get("task_signature"),
                "task_family": thread.get("task_family"),
                "summary": self._truncate(thread.get("summary_text", ""), 240),
            },
            "selected_tools": [str(name) for name in (trace.get("selected_tools") or [])[:8]],
            "selected_skills": [str(name) for name in (trace.get("selected_skills") or [])[:8]],
            "supported_claims": supported_claims,
            "tool_evidence": tool_evidence[:8],
        }

    def _reflection_prompt(
        self,
        *,
        task_signature: str,
        task_family: str,
        thread_id: str | None,
        feedback: str,
        last_prompt: str,
        last_answer: str,
        trace: dict[str, Any],
    ) -> str:
        snapshot = safe_json(self._trace_snapshot(trace))
        return (
            "Diagnose Rocky's previous failure and decide what is worth learning from it.\n\n"
            "Goals:\n"
            "1. Figure out what failed in the previous answer.\n"
            "2. Ground the diagnosis in the provided evidence only.\n"
            "3. Decide what kind of memory should be created: `pattern`, `example`, or `lesson`.\n"
            "4. Decide whether this should become a reusable learned skill (`should_publish_skill`).\n\n"
            "Memory kinds:\n"
            "- `pattern`: reusable correction that should guide future tasks with the same failure shape.\n"
            "- `example`: concrete worked case that is useful to retrieve, but too case-specific to publish as a reusable skill.\n"
            "- `lesson`: raw teacher feedback that should stay in the notebook only.\n\n"
            "Rules:\n"
            "- Use only the provided prompt, answer, feedback, and trace evidence.\n"
            "- Do not invent commands, files, outputs, or hidden reasoning.\n"
            "- Prefer generalized lessons over product-specific wording.\n"
            "- If the lesson does not clearly generalize beyond this exact case, choose `example` or `lesson` and set `should_publish_skill` to false.\n"
            "- Keep `debug_steps` high-signal and concise. They should explain the reasoning flow without hidden chain-of-thought.\n"
            "- Return valid JSON only.\n\n"
            "Return exactly these keys:\n"
            "{"
            '"title": str, '
            '"summary": str, '
            '"failure_class": str, '
            '"root_cause": str, '
            '"corrected_outcome": str, '
            '"generalization_rationale": str, '
            '"evidence": [str], '
            '"debug_steps": [str], '
            '"memory_kind": "pattern"|"example"|"lesson", '
            '"should_publish_skill": bool, '
            '"confidence": float, '
            '"required_behavior": [str], '
            '"prohibited_behavior": [str], '
            '"evidence_requirements": [str], '
            '"triggers": [str], '
            '"keywords": [str]'
            "}\n\n"
            f"Task signature: {task_signature}\n"
            f"Task family: {task_family}\n"
            f"Thread id: {thread_id or ''}\n\n"
            f"Previous prompt:\n{last_prompt[:2400].strip()}\n\n"
            f"Previous answer:\n{last_answer[:3200].strip()}\n\n"
            f"User feedback:\n{feedback[:2400].strip()}\n\n"
            f"Trace snapshot:\n{snapshot}\n"
        )

    def _reflect_with_model(
        self,
        provider: Any,
        *,
        task_signature: str,
        task_family: str,
        thread_id: str | None,
        feedback: str,
        last_prompt: str,
        last_answer: str,
        trace: dict[str, Any],
    ) -> dict[str, Any] | None:
        prompt = self._reflection_prompt(
            task_signature=task_signature,
            task_family=task_family,
            thread_id=thread_id,
            feedback=feedback,
            last_prompt=last_prompt,
            last_answer=last_answer,
            trace=trace,
        )
        system_prompt = (
            "You are Rocky's self-debugging reflection engine. "
            "Diagnose failures, decide what is worth remembering, and return JSON only."
        )
        messages = [Message(role="user", content=prompt)]
        repair_note = (
            "Return valid JSON only with the exact requested keys. "
            "Do not include markdown fences, prose, or commentary."
        )
        for attempt in range(2):
            try:
                response = provider.complete(
                    system_prompt=system_prompt,
                    messages=messages,
                    stream=False,
                    event_handler=None,
                )
            except Exception:
                return None
            candidate = extract_json_candidate(response.text)
            if candidate:
                try:
                    payload = json.loads(candidate)
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    return payload
            messages = [
                Message(role="user", content=prompt),
                Message(role="assistant", content=str(response.text or "")),
                Message(role="user", content=repair_note),
            ]
        return None

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

    def _heuristic_analysis(
        self,
        *,
        task_signature: str,
        feedback: str,
        last_prompt: str,
        last_answer: str,
        trace: dict[str, Any],
        task_family: str,
        thread_id: str | None,
        failure_class: str | None,
    ) -> FeedbackAnalysis:
        tool_names = [str(name) for name in (trace.get("selected_tools") or []) if str(name)]
        feedback_keywords = sorted(tokenize_keywords(feedback))
        prompt_keywords = sorted(tokenize_keywords(last_prompt))
        path_hints = self._path_hints(last_prompt, last_answer, feedback, json.dumps(trace, ensure_ascii=False))
        resolved_failure_class = failure_class or self._failure_class(feedback, trace)
        required_behavior = self._required_behavior(task_signature, feedback, trace)
        prohibited_behavior = self._prohibited_behavior(feedback)
        evidence_requirements = self._evidence_requirements(task_signature)
        evidence = (self._trace_snapshot(trace).get("tool_evidence") or [])[:6]
        triggers: list[str] = []
        for item in [
            task_signature,
            task_family,
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
            task_family=task_family,
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
            root_cause=summary,
            corrected_outcome="Use the feedback to change the next answer's behavior.",
            generalization_rationale="This feedback looks reusable for future tasks with a similar failure shape.",
            evidence=evidence,
            debug_steps=[
                "Recovered the last prompt, answer, and trace from Rocky's persisted runtime state.",
                "Matched the feedback against known failure patterns to classify the mistake.",
                "Compiled reusable do/don't guidance and retrieval triggers from the prompt, feedback, and trace context.",
            ],
            memory_kind="pattern",
            should_publish_skill=True,
            reflection_source="heuristic_fallback",
            confidence=0.35,
        )

    def _analysis_from_payload(
        self,
        payload: dict[str, Any],
        *,
        heuristic: FeedbackAnalysis,
    ) -> FeedbackAnalysis:
        memory_kind = self._memory_kind(payload.get("memory_kind"), heuristic.memory_kind)
        should_publish_skill = self._bool_value(
            payload.get("should_publish_skill"),
            heuristic.should_publish_skill if memory_kind == "pattern" else False,
        )
        triggers = []
        for item in [*self._string_list(payload.get("triggers"), limit=12), *heuristic.triggers]:
            if item and item not in triggers:
                triggers.append(item)
        keywords = []
        for item in [*self._string_list(payload.get("keywords"), limit=12), *heuristic.keywords]:
            if item and item not in keywords:
                keywords.append(item)
        required_behavior = self._string_list(payload.get("required_behavior"), limit=8) or heuristic.required_behavior
        prohibited_behavior = self._string_list(payload.get("prohibited_behavior"), limit=8) or heuristic.prohibited_behavior
        evidence_requirements = self._string_list(payload.get("evidence_requirements"), limit=8) or heuristic.evidence_requirements
        evidence = self._string_list(payload.get("evidence"), limit=8) or heuristic.evidence
        debug_steps = self._string_list(payload.get("debug_steps"), limit=8)
        failure_class = str(payload.get("failure_class") or heuristic.failure_class).strip() or heuristic.failure_class
        title = str(payload.get("title") or heuristic.title).strip() or heuristic.title
        summary = str(payload.get("summary") or heuristic.summary).strip() or heuristic.summary
        root_cause = str(payload.get("root_cause") or heuristic.root_cause or summary).strip() or summary
        corrected_outcome = str(payload.get("corrected_outcome") or heuristic.corrected_outcome).strip()
        generalization_rationale = str(
            payload.get("generalization_rationale")
            or heuristic.generalization_rationale
            or "This correction should guide future tasks with the same failure shape."
        ).strip()
        return FeedbackAnalysis(
            failure_class=failure_class,
            task_signature=heuristic.task_signature,
            task_family=heuristic.task_family,
            title=title,
            summary=summary,
            required_behavior=required_behavior,
            prohibited_behavior=prohibited_behavior,
            evidence_requirements=evidence_requirements,
            triggers=triggers[:16],
            keywords=keywords[:16],
            path_hints=heuristic.path_hints,
            tool_names=heuristic.tool_names,
            prompt_excerpt=heuristic.prompt_excerpt,
            answer_excerpt=heuristic.answer_excerpt,
            feedback_excerpt=heuristic.feedback_excerpt,
            root_cause=root_cause,
            corrected_outcome=corrected_outcome,
            generalization_rationale=generalization_rationale,
            evidence=evidence,
            debug_steps=debug_steps,
            memory_kind=memory_kind,
            should_publish_skill=should_publish_skill,
            reflection_source="model_reflection",
            confidence=self._float_value(payload.get("confidence"), 0.7),
        )

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
        provider: Any | None = None,
    ) -> FeedbackAnalysis:
        trace = trace or {}
        resolved_task_family = task_family or task_signature.split("/", 1)[0]
        heuristic = self._heuristic_analysis(
            task_signature=task_signature,
            feedback=feedback,
            last_prompt=last_prompt,
            last_answer=last_answer,
            trace=trace,
            task_family=resolved_task_family,
            thread_id=thread_id,
            failure_class=failure_class,
        )
        if provider is None or not self.use_model:
            return heuristic
        payload = self._reflect_with_model(
            provider,
            task_signature=task_signature,
            task_family=resolved_task_family,
            thread_id=thread_id,
            feedback=feedback,
            last_prompt=last_prompt,
            last_answer=last_answer,
            trace=trace,
        )
        if not isinstance(payload, dict):
            return heuristic
        return self._analysis_from_payload(payload, heuristic=heuristic)

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
        analysis: FeedbackAnalysis | None = None,
        provider: Any | None = None,
    ) -> SkillDraft:
        analysis = analysis or self.analyze_feedback(
            task_signature,
            feedback,
            last_prompt,
            last_answer,
            trace,
            task_family=task_family,
            thread_id=thread_id,
            failure_class=failure_class,
            provider=provider,
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
            "memory_kind": analysis.memory_kind,
            "should_publish_skill": analysis.should_publish_skill,
            "reflection_source": analysis.reflection_source,
            "reflection_confidence": analysis.confidence,
            "root_cause": analysis.root_cause,
            "generalization_rationale": analysis.generalization_rationale,
            "debug_steps": analysis.debug_steps,
            "evidence": analysis.evidence,
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
        observed_text = "\n".join(f"- {item}" for item in analysis.evidence[:8]) or "- none captured"
        debug_text = "\n".join(f"- {item}" for item in analysis.debug_steps[:8]) or "- none captured"
        path_text = "\n".join(f"- `{path_hint}`" for path_hint in analysis.path_hints[:6]) or "- none captured"
        body = f"""
# Learned corrective workflow

## Why this skill exists

This skill was synthesized from a reflective self-debugging pass over user feedback on a previous Rocky answer.

## Failure class

{analysis.failure_class}

## Memory decision

- kind: `{analysis.memory_kind}`
- publish reusable skill: `{analysis.should_publish_skill}`
- reflection source: `{analysis.reflection_source}`
- confidence: `{analysis.confidence:.2f}`

## Correction from the user

{feedback.strip()}

## Root cause

{analysis.root_cause or analysis.summary}

## Reflection flow

{debug_text}

## Evidence observed

{observed_text}

## Why this generalizes

{analysis.generalization_rationale or "This correction should guide future tasks with the same failure shape."}

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
