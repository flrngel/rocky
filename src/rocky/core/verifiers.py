from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from rocky.core.router import RouteDecision, TaskClass
from rocky.util.text import extract_json_candidate

SCRIPT_REFERENCE_RE = re.compile(
    r"`(?P<quoted>(?:\./)?[a-z0-9_.-]+\.(?:sh|py|rb|js|ts|tsx|pl|php))`"
    r"|(?<![\w/])(?P<bare>(?:\./)?[a-z0-9_.-]+\.(?:sh|py|rb|js|ts|tsx|pl|php))(?![\w/])",
    re.I,
)


@dataclass(slots=True)
class VerificationResult:
    name: str
    status: str
    message: str


class VerifierRegistry:
    RESPONSE_ANALYSIS_PHRASES = (
        " explore ",
        " analyze ",
        " inspect the response",
        " response",
        " decide ",
        " classify ",
        " candidate",
        " merge",
    )
    LIVE_ERROR_MARKERS = (
        '"error":',
        "invalid api token",
        "unauthorized",
        "forbidden",
        "permission denied",
        "connection refused",
        "timed out",
        "timeout",
        "could not resolve",
        "failed to connect",
    )
    FAILURE_ACKNOWLEDGEMENT_PHRASES = (
        "cannot determine",
        "can't determine",
        "could not determine",
        "couldn't determine",
        "unable to determine",
        "cannot decide",
        "can't decide",
        "could not decide",
        "couldn't decide",
        "unable to decide",
        "cannot complete",
        "could not complete",
        "unable to complete",
        "failed to retrieve",
        "couldn't retrieve",
        "not enough information",
        "insufficient information",
        "invalid api token",
        "unauthorized",
        "forbidden",
        "script returned an error",
    )

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

    def _last_successful_shell_command(self, tool_events: list[dict]) -> str:
        for event in reversed(tool_events):
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if event.get("name") != "run_shell_command":
                continue
            payload = self._tool_payload(event)
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            command = str(data.get("command", "")).strip()
            if command:
                return command
        return ""

    def _successful_shell_events(self, tool_events: list[dict]) -> list[dict]:
        return [
            event
            for event in tool_events
            if event.get("type") == "tool_result"
            and event.get("success", True)
            and event.get("name") == "run_shell_command"
        ]

    def _command_text(self, event: dict) -> str:
        payload = self._tool_payload(event)
        data = payload.get("data")
        if not isinstance(data, dict):
            return ""
        return str(data.get("command", "")).strip()

    def _referenced_script_names(self, prompt: str) -> set[str]:
        names: set[str] = set()
        for match in SCRIPT_REFERENCE_RE.finditer(prompt):
            candidate = (match.group("quoted") or match.group("bare") or "").strip()
            if not candidate:
                continue
            names.add(PurePosixPath(candidate).name.lower())
        return names

    def _command_executes_script(self, command: str, script_name: str) -> bool:
        escaped = re.escape(script_name.lower())
        patterns = (
            rf"(^|\s)(?:\./)?{escaped}(?:\s|$)",
            rf"(^|\s)(?:sh|bash|zsh|python|python3|python3\.\d+|ruby|node|php|perl)\s+(?:\./)?{escaped}(?:\s|$)",
            rf"chmod\s+\+x\s+(?:\./)?{escaped}.*(?:&&|;)\s*(?:\./)?{escaped}(?:\s|$)",
        )
        return any(re.search(pattern, command) for pattern in patterns)

    def _script_execution_events(self, prompt: str, tool_events: list[dict]) -> list[dict]:
        script_names = self._referenced_script_names(prompt)
        if not script_names:
            return []
        matches: list[dict] = []
        for event in self._successful_shell_events(tool_events):
            command = self._command_text(event).lower()
            if any(self._command_executes_script(command, script_name) for script_name in script_names):
                matches.append(event)
        return matches

    def _tool_path(self, event: dict) -> str:
        arguments = event.get("arguments") or {}
        return str(arguments.get("path") or "").strip()

    def _is_internal_path(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        return (
            normalized.startswith(".rocky/")
            or normalized.startswith(".git/")
            or "/.rocky/" in normalized
            or "/.git/" in normalized
        )

    def _has_response_analysis_follow_up(self, prompt: str, tool_events: list[dict]) -> bool:
        script_names = self._referenced_script_names(prompt)
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            name = str(event.get("name", ""))
            if name == "run_shell_command":
                continue
            if name == "run_python":
                payload = self._tool_payload(event)
                data = payload.get("data")
                if isinstance(data, dict) and str(data.get("stdout", "")).strip():
                    return True
                continue
            path = self._tool_path(event)
            if name in {"write_file", "stat_path", "read_file"}:
                if not path or self._is_internal_path(path):
                    continue
                if PurePosixPath(path).name.lower() in script_names:
                    continue
                return True
        return False

    def _latest_script_execution_error(self, prompt: str, tool_events: list[dict]) -> str:
        for event in reversed(self._script_execution_events(prompt, tool_events)):
            text = self._shell_output_text(event).lower()
            if any(marker in text for marker in self.LIVE_ERROR_MARKERS):
                return text
        return ""

    def _acknowledges_live_failure(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in self.FAILURE_ACKNOWLEDGEMENT_PHRASES)

    def _mentions_shell_command(self, output: str, command: str) -> bool:
        lowered_output = output.lower()
        if command.lower() in lowered_output:
            return True
        tokens = [token.strip("'\"`()[]{}") for token in command.split()]
        for token in tokens:
            if not token or token.startswith("-"):
                continue
            if "/" in token or "." in token:
                basename = PurePosixPath(token).name.lower()
                if basename and basename in lowered_output:
                    return True
        return False

    def _recovered_after_tool_failures(self, tool_events: list[dict]) -> bool:
        failures = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and not event.get("success", True)
        ]
        if not failures:
            return False
        last_failure_index = max(
            index
            for index, event in enumerate(tool_events)
            if event.get("type") == "tool_result" and not event.get("success", True)
        )
        failed_names = {str(event.get("name", "")) for event in failures if event.get("name")}
        successful_names_after_failure = {
            str(event.get("name", ""))
            for event in tool_events[last_failure_index + 1 :]
            if event.get("type") == "tool_result" and event.get("success", True)
        }
        return bool(failed_names) and failed_names.issubset(successful_names_after_failure)

    def verify(
        self,
        prompt: str,
        route: RouteDecision,
        task_class: TaskClass,
        output: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        result = self._expected_tool_use(route, prompt, output, tool_events)
        if result.status != "pass":
            return result
        result = self._tool_failure(route, tool_events)
        if result.status != "pass":
            return result
        result = self._shell_execution_truthfulness(prompt, route, output, tool_events)
        if result.status != "pass":
            return result
        result = self._structured_output(prompt, output)
        if result.status != "pass":
            return result
        result = self._automation_reporting(prompt, route, output, tool_events)
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
        output: str,
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
            if self._referenced_script_names(prompt) and not self._script_execution_events(prompt, tool_events):
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to successfully execute the referenced workspace script before answering. If `./script` fails, retry with an interpreter such as `sh script.sh` or `python3 tool.py`.",
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
                    " explore ",
                    " analyze ",
                    " response",
                    " decide ",
                    " classify ",
                    " candidate",
                    " merge",
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
            needs_structured_response_follow_up = any(
                phrase in lowered
                for phrase in self.RESPONSE_ANALYSIS_PHRASES
            )
            if needs_structured_response_follow_up and not self._has_response_analysis_follow_up(prompt, tool_events):
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to execute the command and then use a non-shell follow-up tool step on the observed response or a produced result file before deciding",
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
            if "inspect_spreadsheet" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to start spreadsheet analysis with `inspect_spreadsheet` on the named CSV/XLSX file",
                )
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
        if route.task_signature == "automation/general":
            automation_build_terms = ("build", "create", "script", "scaffold", "project", "automation", "repeatable")
            if any(word in lowered for word in automation_build_terms):
                if "write_file" not in result_names:
                    return VerificationResult(
                        "tool_expectation_v1",
                        "fail",
                        "Expected Rocky to create or edit the automation with `write_file` before verifying it",
                    )
            if any(word in lowered for word in ("verify", "execute", "run")) and "run_shell_command" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to verify the automation by executing a shell command",
                )
            if any(word in lowered for word in automation_build_terms):
                if len(successful_names) < 3 or "read_file" not in result_names:
                    return VerificationResult(
                        "tool_expectation_v1",
                        "fail",
                        "Expected Rocky to use at least three automation steps: `write_file`, `read_file`, and `run_shell_command`",
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

    def _shell_execution_truthfulness(
        self,
        prompt: str,
        route: RouteDecision,
        output: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        if route.task_signature != "repo/shell_execution":
            return VerificationResult("shell_truthfulness_v1", "pass", "")
        lowered = prompt.lower()
        if not any(phrase in lowered for phrase in self.RESPONSE_ANALYSIS_PHRASES):
            return VerificationResult("shell_truthfulness_v1", "pass", "")
        script_error = self._latest_script_execution_error(prompt, tool_events)
        if not script_error:
            return VerificationResult("shell_truthfulness_v1", "pass", "")
        if self._acknowledges_live_failure(output):
            return VerificationResult("shell_truthfulness_v1", "pass", "")
        return VerificationResult(
            "shell_truthfulness_v1",
            "fail",
            "Observed script execution returned an error payload. Rocky should say it could not make the requested decision from live evidence instead of inferring from prior context.",
        )

    def _tool_failure(self, route: RouteDecision, tool_events: list[dict]) -> VerificationResult:
        failures = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and not event.get("success", True)
        ]
        if failures and self._recovered_after_tool_failures(tool_events):
            return VerificationResult("tool_failure_v1", "pass", "")
        if failures and route.task_signature == "automation/general":
            successful_names = {
                str(event.get("name", ""))
                for event in tool_events
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            last_failure_index = max(
                index
                for index, event in enumerate(tool_events)
                if event.get("type") == "tool_result" and not event.get("success", True)
            )
            successful_names_after_failure = {
                str(event.get("name", ""))
                for event in tool_events[last_failure_index + 1 :]
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            if (
                "run_shell_command" in successful_names_after_failure
                and successful_names & {"write_file", "run_shell_command"}
            ):
                return VerificationResult("tool_failure_v1", "pass", "")
        if failures and route.task_signature == "repo/shell_execution":
            successful_names = {
                str(event.get("name", ""))
                for event in tool_events
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            last_failure_index = max(
                index
                for index, event in enumerate(tool_events)
                if event.get("type") == "tool_result" and not event.get("success", True)
            )
            successful_names_after_failure = {
                str(event.get("name", ""))
                for event in tool_events[last_failure_index + 1 :]
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            if (
                "run_shell_command" in successful_names
                and "read_file" in successful_names_after_failure
                and successful_names_after_failure & {"run_shell_command", "write_file", "stat_path", "run_python"}
            ):
                return VerificationResult("tool_failure_v1", "pass", "")
        if failures:
            names = ", ".join(sorted({item.get("name", "unknown") for item in failures}))
            return VerificationResult(
                "tool_failure_v1",
                "warn",
                f"Tool failures observed: {names}",
            )
        return VerificationResult("tool_failure_v1", "pass", "")

    def _automation_reporting(
        self,
        prompt: str,
        route: RouteDecision,
        output: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        if route.task_signature != "automation/general":
            return VerificationResult("automation_reporting_v1", "pass", "")
        lowered = prompt.lower()
        if not any(
            phrase in lowered
            for phrase in (
                "exact output",
                "exact json output",
                "valid json output",
                "tell me the exact output",
            )
        ):
            return VerificationResult("automation_reporting_v1", "pass", "")
        command = self._last_successful_shell_command(tool_events)
        if not command:
            return VerificationResult("automation_reporting_v1", "pass", "")
        if self._mentions_shell_command(output, command):
            return VerificationResult("automation_reporting_v1", "pass", "")
        return VerificationResult(
            "automation_reporting_v1",
            "fail",
            "Expected Rocky to mention the exact script or command it verified when reporting the observed automation output",
        )

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
