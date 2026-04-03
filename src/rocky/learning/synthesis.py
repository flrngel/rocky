from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from rocky.util.text import tokenize_keywords


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

    def _trace_chunks(self, trace: dict[str, Any]) -> list[str]:
        chunks: list[str] = []
        for event in trace.get("tool_events") or []:
            if not isinstance(event, dict):
                continue
            name = str(event.get("name") or "").strip()
            arguments = event.get("arguments")
            text = str(event.get("text") or "").strip()
            if name:
                chunks.append(name)
            if isinstance(arguments, dict):
                chunks.extend(str(value) for value in arguments.values() if value)
            if text:
                chunks.append(text[:1200])
        return chunks

    def _path_hints(self, *chunks: str) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for chunk in chunks:
            for match in re.findall(r"(?<![A-Za-z0-9])(?:\.{1,2}/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", chunk or ""):
                cleaned = match.strip(".,:;()[]{}<>`\"'")
                if not cleaned or cleaned in seen:
                    continue
                ordered.append(cleaned)
                seen.add(cleaned)
                if len(ordered) >= 8:
                    return ordered
        return ordered

    def _feedback_checklist(self, feedback: str) -> list[str]:
        rows = [
            item.strip(" -")
            for item in re.split(r"[\n;]+|(?<=[.!?])\s+", feedback.strip())
            if item.strip(" -")
        ]
        return rows[:5]

    def _task_signature_guidance(self, task_signature: str, tool_names: list[str]) -> list[str]:
        if task_signature == "repo/shell_execution":
            return [
                "Execute the named workspace script or command first with `run_shell_command`.",
                "After execution, switch to a non-shell follow-up such as `run_python`, `read_file`, or a reread of any produced JSON before deciding.",
                "Base the final decision only on live output from this turn, not on stale memory or older traces.",
            ]
        if task_signature == "automation/general":
            return [
                "Use `write_file` for the implementation, `read_file` to reread it, and `run_shell_command` to verify the observed output.",
                "Keep created files inside the workspace and name the exact verified command in the answer.",
                "If the observed output is wrong, edit and rerun instead of stopping at a draft.",
            ]
        if task_signature == "repo/general":
            return [
                "Search is only the first step; read the most likely file before answering implementation questions.",
            ]
        if task_signature == "data/spreadsheet/analysis":
            return [
                "Start with `inspect_spreadsheet`, then use `read_sheet_range` or `run_python` before concluding.",
            ]
        if task_signature == "local/runtime_inspection":
            return [
                "Use `inspect_runtime_versions` first, then confirm the claim with a shell command.",
            ]
        guidance: list[str] = []
        if tool_names:
            guidance.append(
                "Prefer the tool flow that already proved useful here: " + ", ".join(tool_names[:4]) + "."
            )
        return guidance

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
    ) -> SkillDraft:
        skill_id = _slug(task_signature.replace("/", "-"))
        path = learned_root / skill_id / "SKILL.md"
        trace = trace or {}
        tool_names = [str(name) for name in (trace.get("selected_tools") or []) if str(name)]
        trace_chunks = self._trace_chunks(trace)
        prompt_keywords = sorted(tokenize_keywords(last_prompt))
        feedback_keywords = sorted(tokenize_keywords(feedback))
        path_hints = self._path_hints(last_prompt, last_answer, feedback, *trace_chunks)
        triggers = []
        for item in [task_signature, *path_hints, *tool_names[:4], *prompt_keywords[:6], *feedback_keywords[:6]]:
            if item and item not in triggers:
                triggers.append(item)
        checklist = self._feedback_checklist(feedback)
        checklist.extend(
            item
            for item in self._task_signature_guidance(task_signature, tool_names)
            if item not in checklist
        )
        description = feedback.strip().splitlines()[0][:120] if feedback.strip() else f"Learned corrective workflow for {task_signature}"
        metadata = {
            "name": skill_id,
            "description": description,
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
                "triggers": triggers[:12],
                "keywords": feedback_keywords[:12],
            },
            "tools": tool_names[:8],
            "paths": path_hints[:8],
        }
        frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
        checklist_text = "\n".join(f"{index}. {item}" for index, item in enumerate(checklist[:6], start=1))
        path_text = "\n".join(f"- `{path_hint}`" for path_hint in path_hints[:6]) or "- none captured"
        body = f"""
# Learned corrective workflow

## Why this skill exists

This skill was synthesized from user feedback on a previous Rocky answer.

## Correction

{feedback.strip()}

## Workspace hints

{path_text}

## Operational guidance

{checklist_text}

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
