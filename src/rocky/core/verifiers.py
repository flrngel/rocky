from __future__ import annotations

import json
from dataclasses import dataclass

from rocky.core.router import RouteDecision, TaskClass
from rocky.util.text import extract_json_candidate


@dataclass(slots=True)
class VerificationResult:
    name: str
    status: str
    message: str


class VerifierRegistry:
    def verify(
        self,
        prompt: str,
        route: RouteDecision,
        task_class: TaskClass,
        output: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        result = self._expected_tool_use(route, prompt, tool_events)
        if result.status != "pass":
            return result
        result = self._tool_failure(route, tool_events)
        if result.status != "pass":
            return result
        result = self._structured_output(prompt, output)
        if result.status != "pass":
            return result
        result = self._citations(task_class, output, tool_events)
        if result.status != "pass":
            return result
        return VerificationResult("default_v1", "pass", "Passed basic verification")

    def _expected_tool_use(
        self,
        route: RouteDecision,
        prompt: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        lowered = prompt.lower()
        result_names = {
            event.get("name")
            for event in tool_events
            if event.get("type") == "tool_result"
        }
        used_tools = bool(result_names)
        if route.task_signature == "repo/shell_execution":
            if "run_shell_command" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to execute the request with the shell tool, but `run_shell_command` was not used",
                )
        if route.task_signature.startswith("repo/shell") and "shell" in route.tool_families:
            if not used_tools:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to inspect or execute with shell tools, but no tools were used",
                )
        if route.task_signature == "local/runtime_inspection":
            if not used_tools:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to inspect the local runtime with tools, but no tools were used",
                )
            if "inspect_runtime_versions" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to inspect the local runtime with `inspect_runtime_versions`",
                )
        if route.task_signature == "automation/general" and any(
            word in lowered for word in ("verify", "execute", "run")
        ):
            if "run_shell_command" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to verify the automation by executing a shell command",
                )
        if route.task_class == TaskClass.REPO and any(
            phrase in lowered
            for phrase in (
                'in this repo',
                'current git status',
                'last commit',
                'what files are modified',
                'find where',
                'function name',
                'implemented',
            )
        ):
            if not used_tools:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to inspect the repo with tools, but no tools were used",
                )
        return VerificationResult("tool_expectation_v1", "pass", "")

    def _tool_failure(self, route: RouteDecision, tool_events: list[dict]) -> VerificationResult:
        failures = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and not event.get("success", True)
        ]
        if (
            failures
            and route.task_signature == "automation/general"
            and all(item.get("name") == "run_shell_command" for item in failures)
        ):
            successful_names = {
                event.get("name")
                for event in tool_events
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            if "run_shell_command" in successful_names and "write_file" in successful_names:
                return VerificationResult(
                    "tool_failure_v1",
                    "pass",
                    "Automation recovered after shell verification retries",
                )
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
            text = extract_json_candidate(output) or output.strip()
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
