from __future__ import annotations

import json
from dataclasses import dataclass
import re

from rocky.core.router import RouteDecision, TaskClass
from rocky.util.text import extract_json_candidate


@dataclass(slots=True)
class VerificationResult:
    name: str
    status: str
    message: str


class VerifierRegistry:
    def _successful_tool_names(self, tool_events: list[dict]) -> list[str]:
        return [
            str(event.get("name", ""))
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]

    def _tool_payload(self, event: dict) -> dict:
        try:
            payload = json.loads(str(event.get("text", "")))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _shell_output_text(self, event: dict) -> str:
        payload = self._tool_payload(event)
        data = payload.get("data")
        if not isinstance(data, dict):
            return ""
        stdout = str(data.get("stdout", "")).strip()
        stderr = str(data.get("stderr", "")).strip()
        return "\n".join(part for part in (stdout, stderr) if part)

    def _is_current_price_prompt(self, prompt: str) -> bool:
        lowered = prompt.lower()
        return any(term in lowered for term in ("price", "stock", "quote")) and any(
            term in lowered for term in ("today", "current", "latest")
        )

    def _has_successful_price_lookup(self, tool_events: list[dict]) -> bool:
        price_line = re.compile(r"^\s*\$?\d+(?:\.\d+)?\s*$")
        csv_quote = re.compile(r"\b[A-Z]{1,6}(?:\.[A-Z]{1,4})?,\d{8},\d{6},[-+]?\d+(?:\.\d+)?", re.I)
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if event.get("name") != "run_shell_command":
                continue
            text = self._shell_output_text(event)
            if not text:
                continue
            if price_line.search(text) or csv_quote.search(text):
                return True
        return False

    def _has_live_lookup_failure_marker(self, tool_events: list[dict]) -> bool:
        markers = (
            "too many requests",
            "429",
            "rate limit",
            "jsondecodeerror",
            "parse error",
            "invalid numeric literal",
        )
        for event in tool_events:
            if event.get("type") != "tool_result" or event.get("name") != "run_shell_command":
                continue
            text = self._shell_output_text(event).lower()
            if any(marker in text for marker in markers):
                return True
        return False

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
        successful_names = self._successful_tool_names(tool_events)
        result_names = set(successful_names)
        used_tools = bool(result_names)
        if route.task_signature == "repo/shell_execution":
            if "run_shell_command" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to execute the request with the shell tool, but `run_shell_command` was not used",
                )
            needs_follow_up = any(
                phrase in lowered
                for phrase in (
                    " then ",
                    " and then ",
                    " after ",
                    " verify ",
                    " confirm ",
                    " inspect ",
                    " read ",
                    " stat ",
                    " count ",
                )
            )
            if needs_follow_up and len(successful_names) < 2:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to execute the command first and then use at least one follow-up tool step to inspect or verify the result",
                )
            if self._is_current_price_prompt(prompt):
                if not self._has_successful_price_lookup(tool_events):
                    if self._has_live_lookup_failure_marker(tool_events):
                        return VerificationResult(
                            "tool_expectation_v1",
                            "fail",
                            "Expected Rocky to retry the current price lookup with another live CLI source after the first source failed or was rate-limited",
                        )
                    return VerificationResult(
                        "tool_expectation_v1",
                        "fail",
                        "Expected Rocky to retrieve the requested current price with a shell command before answering",
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
            if any(
                phrase in lowered
                for phrase in (
                    "command path",
                    "command paths",
                    "where they live",
                    "confirm one with a shell command",
                    "which executable",
                    "which executables",
                )
            ) and not (result_names & {"run_shell_command", "inspect_shell_environment"}):
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to confirm runtime version or path claims with a shell inspection step after `inspect_runtime_versions`",
                )
        if route.task_signature == "data/spreadsheet/analysis":
            if len(successful_names) < 2:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to use at least two spreadsheet-analysis steps before answering",
                )
            needs_follow_up_range = any(
                phrase in lowered
                for phrase in (
                    "sample",
                    "samples",
                    "header",
                    "headers",
                    "compare",
                    "sheet",
                    "sheets",
                    "row count",
                    "total",
                    "sum",
                )
            )
            if needs_follow_up_range and not (result_names & {"read_sheet_range", "run_python"}):
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to follow spreadsheet inspection with `read_sheet_range` or `run_python` for the requested detail",
                )
        if route.task_signature == "extract/general" and len(successful_names) < 2:
            return VerificationResult(
                "tool_expectation_v1",
                "fail",
                "Expected Rocky to use at least two extraction steps before answering",
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
        if failures and route.task_signature == "automation/general":
            successful_names = {
                event.get("name")
                for event in tool_events
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            last_failure_index = max(
                index
                for index, event in enumerate(tool_events)
                if event.get("type") == "tool_result" and not event.get("success", True)
            )
            successful_names_after_failure = {
                event.get("name")
                for event in tool_events[last_failure_index + 1 :]
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            if (
                "run_shell_command" in successful_names_after_failure
                and successful_names & {"write_file", "run_shell_command"}
            ):
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
