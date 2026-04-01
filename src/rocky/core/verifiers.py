from __future__ import annotations

import json
from dataclasses import dataclass

from rocky.core.router import TaskClass


@dataclass(slots=True)
class VerificationResult:
    name: str
    status: str
    message: str


class VerifierRegistry:
    def verify(
        self,
        prompt: str,
        task_class: TaskClass,
        output: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        result = self._tool_failure(tool_events)
        if result.status != "pass":
            return result
        result = self._structured_output(prompt, output)
        if result.status != "pass":
            return result
        result = self._citations(task_class, output, tool_events)
        if result.status != "pass":
            return result
        return VerificationResult("default_v1", "pass", "Passed basic verification")

    def _tool_failure(self, tool_events: list[dict]) -> VerificationResult:
        failures = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and not event.get("success", True)
        ]
        if failures:
            names = ", ".join(sorted({item.get("name", "unknown") for item in failures}))
            return VerificationResult(
                "tool_failure_v1",
                "warn",
                f"Tool failures observed: {names}",
            )
        return VerificationResult("tool_failure_v1", "pass", "")

    def _structured_output(self, prompt: str, output: str) -> VerificationResult:
        lowered = prompt.lower()
        if any(term in lowered for term in ["json", "yaml", "schema", "structured output"]):
            text = output.strip()
            if text.startswith("```"):
                text = "\n".join(
                    line for line in text.splitlines() if not line.strip().startswith("```")
                )
            try:
                json.loads(text)
                return VerificationResult(
                    "structured_output_v1",
                    "pass",
                    "JSON parsed successfully",
                )
            except Exception:
                return VerificationResult(
                    "structured_output_v1",
                    "fail",
                    "Requested structured output but response is not valid JSON",
                )
        return VerificationResult("structured_output_v1", "pass", "")

    def _citations(
        self,
        task_class: TaskClass,
        output: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        used_live_tools = any(
            event.get("name")
            in {
                "fetch_url",
                "search_web",
                "extract_links",
                "browser_render_page",
                "browser_screenshot",
            }
            for event in tool_events
            if event.get("type") == "tool_result"
        )
        if task_class in {TaskClass.RESEARCH, TaskClass.LIVE_COMPARE} or used_live_tools:
            if "http://" not in output and "https://" not in output and "Sources:" not in output:
                return VerificationResult(
                    "citation_hint_v1",
                    "warn",
                    "Live-source task completed without explicit source links",
                )
        return VerificationResult("citation_hint_v1", "pass", "")
