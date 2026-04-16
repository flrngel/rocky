from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
import re

from rocky.core.router import RouteDecision, TaskClass
from rocky.core.runtime_state import (
    ActiveTaskThread,
    AnswerContract,
    EvidenceGraph,
    prompt_requests_list_output,
    requested_minimum_list_items,
)
from rocky.tool_events import tool_event_artifacts, tool_event_payload
from rocky.util.text import extract_json_candidate

SCRIPT_REFERENCE_RE = re.compile(
    r"`(?P<quoted>(?:\./)?[a-z0-9_.-]+\.(?:sh|py|rb|js|ts|tsx|pl|php))`"
    r"|(?<![\w/])(?P<bare>(?:\./)?[a-z0-9_.-]+\.(?:sh|py|rb|js|ts|tsx|pl|php))(?![\w/])",
    re.I,
)
MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")


@dataclass(slots=True)
class VerificationResult:
    name: str
    status: str
    message: str
    failure_class: str | None = None
    unsupported_claim_ids: list[str] = field(default_factory=list)
    missing_evidence_ids: list[str] = field(default_factory=list)
    answer_drift_score: float = 0.0
    memory_promotion_allowed: bool = False
    learning_promotion_allowed: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "failure_class": self.failure_class,
            "unsupported_claim_ids": self.unsupported_claim_ids,
            "missing_evidence_ids": self.missing_evidence_ids,
            "answer_drift_score": self.answer_drift_score,
            "memory_promotion_allowed": self.memory_promotion_allowed,
            "learning_promotion_allowed": self.learning_promotion_allowed,
            "details": self.details,
        }


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
        "could not retrieve",
        "couldn't retrieve",
        "unable to retrieve",
        "not enough information",
        "insufficient information",
        "invalid api token",
        "unauthorized",
        "forbidden",
        "script returned an error",
    )
    MISSING_EVIDENCE_ACKNOWLEDGEMENT_PHRASES = FAILURE_ACKNOWLEDGEMENT_PHRASES + (
        "i don't know",
        "do not know",
        "don't know",
        "not verified",
        "haven't verified",
        "have not verified",
        "without evidence",
        "need to inspect",
        "need to check",
        "need to search",
        "need to look up",
        "need to verify",
    )
    LIVE_RESEARCH_DISCOVERY_PHRASES = (
        "search for",
        "search the web",
        "look up",
        "lookup ",
        "find out",
        "find information about",
        "find info about",
        "tell me about",
        "who is ",
        "who are ",
        "biography",
        "biographies",
        "leader",
        "leaders",
        "member",
        "members",
    )

    def _successful_tool_names(self, tool_events: list[dict]) -> list[str]:
        return [
            str(event.get("name", ""))
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]

    def _tool_payload(self, event: dict) -> dict:
        return tool_event_payload(event, exact=True)

    def _tool_error_code(self, event: dict) -> str:
        payload = self._tool_payload(event)
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            return str(metadata.get("error") or "").strip()
        return ""

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

    def _live_cli_source_hosts(self, tool_events: list[dict]) -> set[str]:
        hosts: set[str] = set()
        for event in tool_events:
            if event.get("type") != "tool_result" or event.get("name") != "run_shell_command":
                continue
            command = self._command_text(event)
            if not command:
                continue
            for host in re.findall(r"https?://([^/'\"`\s]+)", command, flags=re.I):
                hosts.add(host.lower())
        return hosts

    def _live_cli_source_attempts(self, tool_events: list[dict]) -> list[str]:
        attempts: list[str] = []
        for event in tool_events:
            if event.get("type") != "tool_result" or event.get("name") != "run_shell_command":
                continue
            command = self._command_text(event)
            if re.search(r"https?://", command, flags=re.I):
                attempts.append(command)
        return attempts

    def _can_gracefully_fail_current_price_lookup(self, output: str, tool_events: list[dict]) -> bool:
        if not self._acknowledges_live_failure(output):
            return False
        return (
            len(self._live_cli_source_hosts(tool_events)) >= 2
            and len(self._live_cli_source_attempts(tool_events)) >= 3
        )

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

    def _strip_numbered_read_output(self, text: str) -> str:
        cleaned_lines = []
        for line in text.splitlines():
            cleaned_lines.append(re.sub(r"^\s*\d+:\s?", "", line))
        return "\n".join(cleaned_lines).strip()

    def _prompt_json_file_paths(self, prompt: str) -> list[str]:
        return re.findall(r"`([^`]+\.json)`", prompt, flags=re.I)

    def _prompt_required_json_keys(self, prompt: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        def _quoted_names(fragment: str) -> tuple[str, ...]:
            return tuple(dict.fromkeys(re.findall(r"`([^`]+)`", fragment)))

        top_level_keys: tuple[str, ...] = ()
        item_keys: tuple[str, ...] = ()

        top_level_match = re.search(
            r"top-level key[s]?\s+(?P<fragment>.+?)(?:\s+where\b|[.;]|$)",
            prompt,
            flags=re.I,
        )
        if top_level_match:
            top_level_keys = _quoted_names(top_level_match.group("fragment"))

        item_match = re.search(
            r"each item contains\s+(?P<fragment>.+?)(?:\s+array|\s+arrays|[.;]|$)",
            prompt,
            flags=re.I,
        )
        if item_match:
            item_keys = _quoted_names(item_match.group("fragment"))

        return top_level_keys, item_keys

    def _latest_read_json_payload(self, prompt: str, tool_events: list[dict]) -> tuple[str, Any] | None:
        json_paths = self._prompt_json_file_paths(prompt)
        candidate_paths = {PurePosixPath(path).name for path in json_paths}
        for event in reversed(tool_events):
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if str(event.get("name") or "") != "read_file":
                continue
            path = self._tool_path(event)
            if candidate_paths and PurePosixPath(path).name not in candidate_paths:
                continue
            payload = self._tool_payload(event)
            data = payload.get("data")
            if not isinstance(data, str):
                continue
            candidate = extract_json_candidate(self._strip_numbered_read_output(data))
            if not candidate:
                continue
            try:
                return path, json.loads(candidate)
            except Exception:
                continue
        return None

    def _contains_required_item_keys(self, payload: Any, top_level_keys: tuple[str, ...], item_keys: tuple[str, ...]) -> tuple[bool, str]:
        if not item_keys:
            return True, ""
        containers: list[Any] = []
        if top_level_keys and isinstance(payload, dict):
            for key in top_level_keys:
                value = payload.get(key)
                if isinstance(value, list):
                    containers.extend(value)
        elif isinstance(payload, list):
            containers.extend(payload)
        if not containers:
            return True, ""
        for index, item in enumerate(containers, start=1):
            if not isinstance(item, dict):
                return False, f"expected each item to be an object, but item {index} is {type(item).__name__}"
            missing = [key for key in item_keys if key not in item]
            if missing:
                return False, f"missing item keys {missing} in item {index}"
        return True, ""

    def _is_internal_path(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        return (
            normalized.startswith(".rocky/")
            or normalized.startswith(".git/")
            or "/.rocky/" in normalized
            or "/.git/" in normalized
        )

    def _response_analysis_follow_up_in_events(self, prompt: str, events: list[dict]) -> bool:
        script_names = self._referenced_script_names(prompt)
        for event in events:
            name = str(event.get("name", ""))
            if name == "run_shell_command":
                if self._is_analysis_shell_follow_up(prompt, event, script_names=script_names):
                    return True
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

    def _is_analysis_shell_follow_up(
        self,
        prompt: str,
        event: dict,
        *,
        script_names: set[str] | None = None,
    ) -> bool:
        command = self._command_text(event).lower()
        if not command:
            return False
        analysis_markers = (
            "|",
            "<<",
            "python ",
            "python3",
            "jq ",
            "awk ",
            "perl ",
            "ruby ",
            "node ",
            "php ",
            "grep ",
            "rg ",
            "sed ",
            "cut ",
            "wc ",
            "head ",
            "tail ",
            "tee ",
            "json.",
            "json ",
        )
        if any(marker in command for marker in analysis_markers):
            return True
        referenced = script_names or self._referenced_script_names(prompt)
        if referenced and any(self._command_executes_script(command, script_name) for script_name in referenced):
            return any(marker in command for marker in ("cat ", "grep ", "rg ", "wc ", "head ", "tail "))
        return False

    def _has_response_analysis_follow_up(
        self,
        prompt: str,
        tool_events: list[dict],
        *,
        within_successful_results: int | None = None,
    ) -> bool:
        successful_events = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]
        if within_successful_results is not None:
            successful_events = successful_events[:within_successful_results]
        return self._response_analysis_follow_up_in_events(prompt, successful_events)

    def _has_response_analysis_follow_up_after_last_execution(
        self,
        prompt: str,
        tool_events: list[dict],
        *,
        window: int = 5,
    ) -> bool:
        successful_events = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]
        if not successful_events:
            return False
        anchor_event = None
        script_events = self._script_execution_events(prompt, tool_events)
        if script_events:
            anchor_event = script_events[-1]
        else:
            shell_events = self._successful_shell_events(tool_events)
            if shell_events:
                anchor_event = shell_events[-1]
        if anchor_event is None:
            return False
        try:
            anchor_index = successful_events.index(anchor_event)
        except ValueError:
            return False
        follow_up_events = successful_events[anchor_index + 1 : anchor_index + 1 + window]
        return self._response_analysis_follow_up_in_events(prompt, follow_up_events)

    def _latest_script_execution_error(self, prompt: str, tool_events: list[dict]) -> str:
        for event in reversed(self._script_execution_events(prompt, tool_events)):
            text = self._shell_output_text(event).lower()
            if any(marker in text for marker in self.LIVE_ERROR_MARKERS):
                return text
        return ""

    def _acknowledges_live_failure(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in self.FAILURE_ACKNOWLEDGEMENT_PHRASES)

    def _acknowledges_missing_evidence(self, output: str) -> bool:
        lowered = output.lower()
        return any(marker in lowered for marker in self.MISSING_EVIDENCE_ACKNOWLEDGEMENT_PHRASES)

    def _is_knowledge_request(self, route: RouteDecision, prompt: str) -> bool:
        lowered = prompt.lower().strip()
        if route.task_signature in {
            "research/live_compare/general",
            "site/understanding/general",
        }:
            return True
        if route.task_signature.startswith("repo/"):
            return any(
                phrase in lowered
                for phrase in (
                    "what ",
                    "which ",
                    "show ",
                    "find ",
                    "where ",
                    "who ",
                    "list ",
                    "tell me",
                    "current ",
                    "latest ",
                    "status",
                    "version",
                    "versions",
                    "installed",
                    "history",
                    "commit",
                    "commits",
                )
            )
        return False

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

    def _successful_live_url_count(self, tool_events: list[dict]) -> int:
        urls: set[str] = set()
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            name = str(event.get("name", ""))
            if name not in {"fetch_url", "browser_render_page", "agent_browser"}:
                continue
            arguments = event.get("arguments") or {}
            payload = self._tool_payload(event)
            data = payload.get("data")
            url = str(arguments.get("url") or "").strip()
            if not url and isinstance(data, dict):
                url = str(data.get("url") or data.get("final_url") or "").strip()
            if url:
                urls.add(url)
        return len(urls)

    def _route_validity(
        self,
        prompt: str,
        route: RouteDecision,
        active_thread: ActiveTaskThread | None,
        continuation_expected: bool,
    ) -> VerificationResult:
        if active_thread is None or not continuation_expected:
            return VerificationResult("route_validity_v1", "pass", "")
        short_follow_up = len(prompt.split()) <= 18
        if short_follow_up and route.continued_thread_id is None and route.task_signature.startswith("conversation/"):
            return VerificationResult(
                "route_validity_v1",
                "fail",
                "Expected Rocky to continue the active task thread instead of degrading into generic chat.",
                failure_class="continuation_lost_after_tool_backed_work",
            )
        return VerificationResult("route_validity_v1", "pass", "")

    def _extract_output_claims(self, output: str) -> list[str]:
        lines = [line.rstrip() for line in output.splitlines()]
        non_empty = [line.strip() for line in lines if line.strip()]
        if len(non_empty) == 1 and len(non_empty[0]) > 600:
            lines = re.split(r"(?<=[.!?])\s+", non_empty[0])
        claims: list[str] = []
        for index, line in enumerate(lines):
            raw = line.strip()
            if not raw:
                continue
            if raw.lstrip().startswith("#"):
                continue
            if MARKDOWN_TABLE_SEPARATOR_RE.match(raw):
                continue
            if "|" in raw and raw.count("|") >= 2:
                next_non_empty = ""
                for candidate in lines[index + 1 :]:
                    if candidate.strip():
                        next_non_empty = candidate.strip()
                        break
                if next_non_empty and MARKDOWN_TABLE_SEPARATOR_RE.match(next_non_empty):
                    continue
            stripped = raw.strip(' -*#	')
            if not stripped:
                continue
            if stripped.startswith("{") or stripped.startswith("["):
                continue
            if stripped.lower().startswith(("sources:", "note:", "warning:")):
                continue
            plain = re.sub(r"[`*_]+", "", stripped).strip()
            plain_tokens = re.findall(r"[a-z0-9_.+-]+", plain.lower())
            if plain.endswith(":") and len(plain_tokens) <= 6:
                continue
            if re.match(r"^the following .{1,80}\b(?:was|were) found:$", plain, flags=re.I):
                continue
            claims.append(stripped[:280])
        return claims[:12]

    def _output_list_count(self, output: str) -> int:
        return len(self._output_list_item_lines(output))

    def _output_list_item_lines(self, output: str) -> list[str]:
        lines: list[str] = []
        in_sources = False
        for line in output.splitlines():
            stripped = line.strip()
            plain = re.sub(r"[`*_#]+", "", stripped).strip().lower()
            if plain.startswith(("sources:", "source:", "references:", "reference:", "citations:", "citation:")):
                in_sources = True
                continue
            if in_sources:
                continue
            if LIST_ITEM_RE.match(line):
                lines.append(stripped)
        return lines

    def _research_under_parameter_limit(self, prompt: str) -> float | None:
        match = re.search(r"\b(?:under|below|less than)\s+(\d+(?:\.\d+)?)\s*b\b", prompt, flags=re.I)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _parameter_b_sizes(self, text: str) -> list[float]:
        sizes: list[float] = []
        for match in re.finditer(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b\b", text, flags=re.I):
            try:
                sizes.append(float(match.group(1)))
            except ValueError:
                continue
        return sizes

    def _urls_in_text(self, text: str) -> list[str]:
        return [match.rstrip(").,;:!?]") for match in re.findall(r"https?://[^\s)]+", text or "")]

    def _research_counted_list_item_grounding(
        self,
        prompt: str,
        route: RouteDecision,
        output: str,
        tool_events: list[dict],
        evidence_graph: EvidenceGraph | None,
    ) -> VerificationResult:
        minimum_items = requested_minimum_list_items(prompt)
        if minimum_items <= 0 or not route.task_signature.startswith(("research/", "site/")):
            return VerificationResult("research_list_grounding_v1", "pass", "")
        item_lines = self._output_list_item_lines(output)
        if len(item_lines) < minimum_items:
            return VerificationResult("research_list_grounding_v1", "pass", "")

        artifact_urls: set[str] = set()
        for event in tool_events:
            if event.get("type") != "tool_result":
                continue
            for artifact in tool_event_artifacts(event):
                if str(artifact.get("kind") or "") == "url":
                    ref = str(artifact.get("ref") or "").strip().rstrip(").,;:!?]")
                    if ref:
                        artifact_urls.add(ref)
            if str(event.get("name") or "") in {"fetch_url", "browser_render_page", "agent_browser", "extract_links"}:
                payload = tool_event_payload(event, exact=True)
                data = payload.get("data")
                if isinstance(data, dict):
                    for key in ("url", "final_url"):
                        ref = str(data.get(key) or "").strip().rstrip(").,;:!?]")
                        if ref:
                            artifact_urls.add(ref)
                    raw_items = [item for item in list(data.get("link_items") or []) if isinstance(item, dict)]
                    raw_items.extend(item for item in list(data.get("items") or []) if isinstance(item, dict))
                elif isinstance(data, list):
                    raw_items = [item for item in data if isinstance(item, dict)]
                else:
                    raw_items = []
                for item in raw_items:
                    ref = str(item.get("url") or "").strip().rstrip(").,;:!?]")
                    if ref:
                        artifact_urls.add(ref)

        live_claim_texts: list[str] = []
        if evidence_graph is not None:
            live_sources = {"fetch_url", "browser_render_page", "agent_browser"}
            live_claim_texts = [
                str(claim.text or "")
                for claim in evidence_graph.claims
                if claim.status in {"active", "provisional"} and claim.provenance_source in live_sources
            ]
        live_claim_lower = [text.lower() for text in live_claim_texts]

        bad_urls: list[int] = []
        unsupported: list[int] = []
        bad_params: list[int] = []
        missing_params: list[int] = []
        markup_leaks: list[int] = []
        modality_mismatches: list[int] = []
        under_limit = self._research_under_parameter_limit(prompt)
        wants_text_models = bool(re.search(r"\btext[- ]?(?:generation\s+)?models?\b|\btext-generation\b", prompt, flags=re.I))

        for index, line in enumerate(item_lines[:minimum_items], start=1):
            if re.search(r"</?[a-z][^>]*>", line, flags=re.I):
                markup_leaks.append(index)
            urls = self._urls_in_text(line)
            if urls and not any(url in artifact_urls for url in urls):
                bad_urls.append(index)
                continue

            line_lower = line.lower()
            tokens = {
                token
                for token in re.findall(r"[a-z0-9_.+-]+", line_lower)
                if len(token) >= 3 and token not in {"http", "https", "huggingface", "link", "model", "models"}
            }
            supported_by_url = bool(urls and any(url in artifact_urls for url in urls))
            supported_by_claim = any(len(tokens & set(re.findall(r"[a-z0-9_.+-]+", claim))) >= 2 for claim in live_claim_lower)
            if not supported_by_url and not supported_by_claim:
                unsupported.append(index)

            if under_limit is not None:
                sizes = self._parameter_b_sizes(line)
                if not sizes and urls:
                    related_claims = [text for text in live_claim_texts if any(url in text for url in urls)]
                    for claim_text in related_claims:
                        sizes.extend(self._parameter_b_sizes(claim_text))
                if not sizes:
                    missing_params.append(index)
                elif any(size >= under_limit for size in sizes):
                    bad_params.append(index)
            if wants_text_models and any(marker in line_lower for marker in ("text-to-image", "image-text-to-text", "image-to-text", "image-to-image")):
                modality_mismatches.append(index)

        if markup_leaks:
            return VerificationResult(
                "research_list_grounding_v1",
                "fail",
                f"Final research list contains leaked markup in item(s): {markup_leaks}. Rewrite those items from clean observed labels.",
                failure_class="research_list_item_markup_leak",
                unsupported_claim_ids=[f"list_item_{index}" for index in markup_leaks],
            )
        if bad_urls:
            return VerificationResult(
                "research_list_grounding_v1",
                "fail",
                f"Final research list contains URL(s) that were not observed in opened-page evidence at item(s): {bad_urls}. Use exact observed URLs from tool artifacts.",
                failure_class="research_list_item_url_unverified",
                unsupported_claim_ids=[f"list_item_{index}" for index in bad_urls],
            )
        if bad_params or missing_params:
            detail = []
            if bad_params:
                detail.append(f"items violating the numeric parameter filter: {bad_params}")
            if missing_params:
                detail.append(f"items missing parameter evidence: {missing_params}")
            return VerificationResult(
                "research_list_grounding_v1",
                "fail",
                "Final research list does not verify the requested numeric filter for every listed item: " + "; ".join(detail) + ".",
                failure_class="research_list_parameter_filter_unverified",
                unsupported_claim_ids=[f"list_item_{index}" for index in [*bad_params, *missing_params]],
            )
        if modality_mismatches:
            return VerificationResult(
                "research_list_grounding_v1",
                "fail",
                f"Final research list includes item(s) whose stated modality conflicts with the requested text-model filter: {modality_mismatches}.",
                failure_class="research_list_task_type_unverified",
                unsupported_claim_ids=[f"list_item_{index}" for index in modality_mismatches],
            )
        if unsupported:
            return VerificationResult(
                "research_list_grounding_v1",
                "fail",
                f"Final research list contains item(s) that do not map to opened-page evidence: {unsupported}.",
                failure_class="research_list_item_not_grounded",
                unsupported_claim_ids=[f"list_item_{index}" for index in unsupported],
            )
        return VerificationResult("research_list_grounding_v1", "pass", "")

    def _claim_support(
        self,
        output: str,
        evidence_graph: EvidenceGraph | None,
        answer_contract: AnswerContract | None,
        route: RouteDecision,
    ) -> VerificationResult:
        if evidence_graph is None or not evidence_graph.claims:
            return VerificationResult("claim_support_v1", "pass", "")
        supported_claims = [claim for claim in evidence_graph.claims if claim.status in {"active", "provisional"}]
        if not supported_claims:
            return VerificationResult("claim_support_v1", "pass", "")
        allowed_ids = set(answer_contract.allowed_claim_ids if answer_contract else [claim.claim_id for claim in supported_claims])
        allowed_claims = [claim for claim in supported_claims if claim.claim_id in allowed_ids] or supported_claims
        allowed_claim_tokens = [
            set(re.findall(r"[a-z0-9_.+-]+", claim.text.lower()))
            for claim in allowed_claims
        ]
        unsupported: list[str] = []
        for index, claim_text in enumerate(self._extract_output_claims(output), start=1):
            lowered = claim_text.lower()
            if any(term in lowered for term in ("i'm not sure", "not sure", "unclear", "cannot determine", "can't determine", "maybe ", "might ", "could ")):
                continue
            claim_tokens = set(re.findall(r"[a-z0-9_.+-]+", lowered))
            if len(claim_tokens) < 3:
                continue
            overlaps = sorted(
                (len(claim_tokens & tokens) for tokens in allowed_claim_tokens),
                reverse=True,
            )
            best_overlap = overlaps[0] if overlaps else 0
            ranked_tokens = sorted(
                allowed_claim_tokens,
                key=lambda item: len(claim_tokens & item),
                reverse=True,
            )[:3]
            combined_tokens: set[str] = set()
            contributing_claims = 0
            for tokens in ranked_tokens:
                if claim_tokens & tokens:
                    contributing_claims += 1
                combined_tokens |= tokens
            combined_overlap = len(claim_tokens & combined_tokens)
            threshold = 2 if route.task_signature.startswith(("repo/", "local/", "data/", "extract/", "automation/", "research/", "site/")) else 1
            if best_overlap < threshold and not (
                combined_overlap >= threshold and contributing_claims >= 2
            ):
                unsupported.append(f"output_claim_{index}")
        if unsupported:
            return VerificationResult(
                "claim_support_v1",
                "fail",
                "Final answer includes unsupported deterministic claims that do not map cleanly to evidence-bearing claims.",
                failure_class="unsupported_claim_introduced",
                unsupported_claim_ids=unsupported,
            )
        return VerificationResult("claim_support_v1", "pass", "")

    def _answer_discipline(
        self,
        prompt: str,
        output: str,
        answer_contract: AnswerContract | None,
        prior_answer: str | None,
    ) -> VerificationResult:
        if answer_contract is None:
            return VerificationResult("answer_discipline_v1", "pass", "")
        prompt_tokens = set(re.findall(r"[a-z0-9_.+-]+", prompt.lower()))
        output_tokens = set(re.findall(r"[a-z0-9_.+-]+", output.lower()))
        prior_tokens = set(re.findall(r"[a-z0-9_.+-]+", (prior_answer or "").lower()))
        prompt_overlap = len(prompt_tokens & output_tokens) / max(len(output_tokens) or 1, 1)
        prior_overlap = len(prior_tokens & output_tokens) / max(len(output_tokens) or 1, 1) if prior_tokens else 0.0
        drift = max(0.0, prior_overlap - prompt_overlap)
        if answer_contract.do_not_repeat_context and drift > 0.35 and len(output.split()) > 40:
            return VerificationResult(
                "answer_discipline_v1",
                "fail",
                "Answer recapped prior context instead of answering the current ask directly.",
                failure_class="answer_recapped_previous_context",
                answer_drift_score=drift,
            )
        if answer_contract.missing_evidence and not answer_contract.uncertainty_required:
            return VerificationResult("answer_discipline_v1", "pass", "", answer_drift_score=drift)
        return VerificationResult("answer_discipline_v1", "pass", "", answer_drift_score=drift)

    def _evidence_discipline(
        self,
        prompt: str,
        route: RouteDecision,
        output: str,
        answer_contract: AnswerContract | None,
    ) -> VerificationResult:
        if answer_contract is None or not answer_contract.missing_evidence or not answer_contract.uncertainty_required:
            return VerificationResult("evidence_discipline_v1", "pass", "")
        if not self._is_knowledge_request(route, prompt):
            return VerificationResult("evidence_discipline_v1", "pass", "")
        if self._acknowledges_missing_evidence(output):
            return VerificationResult("evidence_discipline_v1", "pass", "")
        return VerificationResult(
            "evidence_discipline_v1",
            "fail",
            "Rocky answered as if the requested facts were known, but supporting evidence is still missing. Rocky must use tools to gather references first, or explicitly say it cannot determine the answer from evidence yet.",
            failure_class="answer_claimed_knowledge_without_reference",
            missing_evidence_ids=list(answer_contract.missing_evidence),
        )

    def _list_requirements(
        self,
        prompt: str,
        route: RouteDecision,
        output: str,
        tool_events: list[dict],
        answer_contract: AnswerContract | None,
    ) -> VerificationResult:
        minimum_items = requested_minimum_list_items(prompt)
        if minimum_items <= 0 and not prompt_requests_list_output(prompt):
            return VerificationResult("list_requirements_v1", "pass", "")
        observed_count = self._output_list_count(output)
        if minimum_items > 0 and observed_count >= minimum_items and self._acknowledges_missing_evidence(output):
            return VerificationResult(
                "list_requirements_v1",
                "fail",
                "Final answer gives the requested counted list but also says enough verified items were not found. Remove the contradictory insufficiency statement or continue gathering before answering.",
                failure_class="counted_list_contradictory_insufficiency",
            )
        if self._acknowledges_missing_evidence(output):
            if route.task_signature.startswith(("research/", "site/")) and minimum_items > 0:
                successful_live_steps = sum(
                    1
                    for event in tool_events
                    if event.get("type") == "tool_result"
                    and event.get("success", True)
                    and str(event.get("name", "")) in {"search_web", "fetch_url", "browser_render_page", "agent_browser"}
                )
                if successful_live_steps < 5 or self._successful_live_url_count(tool_events) < 3:
                    return VerificationResult(
                        "list_requirements_v1",
                        "fail",
                        "Rocky stopped the counted live-research search too early. Continue with additional live pages or filters before concluding that not enough verified items exist.",
                        failure_class="counted_list_search_stopped_too_early",
                    )
            return VerificationResult("list_requirements_v1", "pass", "")
        if minimum_items > 0 and observed_count < minimum_items:
            return VerificationResult(
                "list_requirements_v1",
                "fail",
                f"Expected at least {minimum_items} list items in the final answer, or an explicit statement that enough verified items could not be found yet.",
                failure_class="minimum_list_count_not_met",
            )
        if prompt_requests_list_output(prompt) and observed_count == 0:
            return VerificationResult(
                "list_requirements_v1",
                "fail",
                "Expected the final answer to be formatted as a list.",
                failure_class="requested_list_format_missing",
            )
        if (
            route.task_signature.startswith(("research/", "site/"))
            and minimum_items > 0
            and answer_contract is not None
            and answer_contract.missing_evidence
        ):
            return VerificationResult(
                "list_requirements_v1",
                "fail",
                "Rocky presented a complete counted list even though live item evidence is still missing. Gather more live item evidence or say clearly that you could not verify enough items yet.",
                failure_class="counted_list_missing_live_evidence",
                missing_evidence_ids=list(answer_contract.missing_evidence),
            )
        if route.task_signature.startswith(("research/", "site/")) and minimum_items > 0:
            if self._successful_live_url_count(tool_events) < 2:
                return VerificationResult(
                    "list_requirements_v1",
                    "fail",
                    "Counted live-research lists must come from more than one opened live page before the answer can be treated as complete.",
                    failure_class="counted_list_live_pages_too_shallow",
                )
        return VerificationResult("list_requirements_v1", "pass", "")

    def verify(
        self,
        prompt: str,
        route: RouteDecision,
        task_class: TaskClass,
        output: str,
        tool_events: list[dict],
        *,
        active_thread: ActiveTaskThread | None = None,
        evidence_graph: EvidenceGraph | None = None,
        answer_contract: AnswerContract | None = None,
        prior_answer: str | None = None,
        continuation_expected: bool = False,
        config: Any = None,
    ) -> VerificationResult:
        result = self._route_validity(prompt, route, active_thread, continuation_expected)
        if result.status != "pass":
            return result
        result = self._expected_tool_use(route, prompt, output, tool_events)
        if result.status != "pass":
            return result
        result = self._tool_failure(prompt, route, output, tool_events)
        if result.status != "pass":
            return result
        result = self._shell_execution_truthfulness(prompt, route, output, tool_events)
        if result.status != "pass":
            return result
        result = self._structured_output(prompt, output)
        if result.status != "pass":
            return result
        result = self._json_file_contract(prompt, route, tool_events)
        if result.status != "pass":
            return result
        result = self._automation_reporting(prompt, route, output, tool_events)
        if result.status != "pass":
            return result
        result = self._citations(task_class, output, tool_events)
        if result.status != "pass":
            return result
        result = self._non_empty_output(prompt, output)
        if result.status != "pass":
            result.memory_promotion_allowed = False
            result.learning_promotion_allowed = False
            return result
        result = self._list_requirements(prompt, route, output, tool_events, answer_contract)
        if result.status != "pass":
            result.memory_promotion_allowed = False
            result.learning_promotion_allowed = False
            return result
        result = self._research_counted_list_item_grounding(prompt, route, output, tool_events, evidence_graph)
        if result.status != "pass":
            result.memory_promotion_allowed = False
            result.learning_promotion_allowed = False
            return result
        result = self._evidence_discipline(prompt, route, output, answer_contract)
        if result.status != "pass":
            result.memory_promotion_allowed = False
            result.learning_promotion_allowed = False
            return result
        result = self._claim_support(output, evidence_graph, answer_contract, route)
        if result.status != "pass":
            result.memory_promotion_allowed = False
            result.learning_promotion_allowed = False
            return result
        discipline = self._answer_discipline(prompt, output, answer_contract, prior_answer)
        if discipline.status != "pass":
            discipline.memory_promotion_allowed = False
            discipline.learning_promotion_allowed = False
            return discipline
        memory_allowed = not bool((answer_contract.missing_evidence if answer_contract else [])) and not bool(result.unsupported_claim_ids)
        learning_allowed = True
        default_result = VerificationResult(
            "default_v1",
            "pass",
            "Passed verification",
            answer_drift_score=discipline.answer_drift_score,
            memory_promotion_allowed=memory_allowed,
            learning_promotion_allowed=learning_allowed,
        )
        # Run semantic_research_v1 for research/* routes when enabled.
        semantic_enabled = True
        semantic_threshold = 0.5
        if config is not None:
            verifier_cfg = getattr(config, "verifier", None)
            if verifier_cfg is not None:
                semantic_enabled = bool(getattr(verifier_cfg, "semantic_enabled", True))
                semantic_threshold = float(getattr(verifier_cfg, "semantic_threshold", 0.5))
        if semantic_enabled and route.task_signature.startswith("research/"):
            return self._run_semantic_research_v1(
                output, tool_events, route, semantic_threshold, default_result
            )
        return default_result

    # ------------------------------------------------------------------
    # semantic_research_v1
    # ------------------------------------------------------------------

    _PROPER_NOUN_RE = re.compile(
        r'"[^"]{3,}"'                              # quoted verbatim claims
        r'|'
        r'\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)+\b',  # multi-word proper nouns
    )

    def _extract_claims(self, text: str) -> list[str]:
        """Return a deduplicated list of candidate factual claims from *text*.

        A "claim" here is either a quoted string (≥3 chars) or a run of two+
        consecutive capitalised tokens (proper noun / named entity heuristic).
        """
        seen: set[str] = set()
        claims: list[str] = []
        for m in self._PROPER_NOUN_RE.finditer(text):
            claim = m.group(0).strip().strip('"')
            if claim and claim not in seen:
                seen.add(claim)
                claims.append(claim)
        return claims

    def _run_semantic_research_v1(
        self,
        output: str,
        tool_events: list[dict],
        route: RouteDecision,
        threshold: float,
        default_result: VerificationResult,
    ) -> VerificationResult:
        """Merge semantic claim-grounding check with *default_result*.

        Extracts factual claims from *output*, checks each against tool event
        payloads via token overlap (min_overlap=2), flags unsupported claims,
        and escalates status to ``needs_review`` when the unsupported fraction
        exceeds *threshold*.  Always preserves ``default_result`` fields inside
        ``details["default_v1"]``.
        """
        from rocky.util.evidence import ground_evidence_citations

        # When the answer explicitly acknowledges uncertainty or missing evidence,
        # the answer is not making unsupported factual claims — skip semantic check.
        if self._acknowledges_missing_evidence(output):
            return VerificationResult(
                name="semantic_research_v1",
                status=default_result.status,
                message=default_result.message,
                failure_class=default_result.failure_class,
                unsupported_claim_ids=[],
                missing_evidence_ids=default_result.missing_evidence_ids,
                answer_drift_score=default_result.answer_drift_score,
                memory_promotion_allowed=default_result.memory_promotion_allowed,
                learning_promotion_allowed=default_result.learning_promotion_allowed,
                details={**default_result.details, "default_v1": default_result.as_record()},
            )

        all_claims = self._extract_claims(output)
        supported: list[str] = []
        if all_claims:
            supported = ground_evidence_citations(
                all_claims,
                tool_events,
                direction="claim",
                min_overlap=2,
            )
        supported_set = set(supported)
        unsupported = [c for c in all_claims if c not in supported_set]
        fraction = len(unsupported) / max(1, len(all_claims)) if all_claims else 0.0

        status = default_result.status
        message = default_result.message
        if fraction > threshold:
            status = "needs_review"
            message = (
                f"semantic_research_v1: {len(unsupported)} of {len(all_claims)} "
                f"claims could not be grounded in fetched source payloads "
                f"(fraction={fraction:.2f} > threshold={threshold:.2f}). "
                "Answer may contain unsupported assertions."
            )

        merged = VerificationResult(
            name="semantic_research_v1",
            status=status,
            message=message,
            failure_class="unsupported_claims" if status == "needs_review" else default_result.failure_class,
            unsupported_claim_ids=unsupported,
            missing_evidence_ids=default_result.missing_evidence_ids,
            answer_drift_score=default_result.answer_drift_score,
            memory_promotion_allowed=default_result.memory_promotion_allowed and status == "pass",
            learning_promotion_allowed=default_result.learning_promotion_allowed,
            details={**default_result.details, "default_v1": default_result.as_record()},
        )
        return merged

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
            if needs_structured_response_follow_up and not self._has_response_analysis_follow_up(
                prompt,
                tool_events,
                within_successful_results=5,
            ) and not self._has_response_analysis_follow_up_after_last_execution(
                prompt,
                tool_events,
                window=5,
            ):
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to execute the command and then use a follow-up analysis step within the first five successful tool results, or within five successful steps after the final execution retry, on the observed response or a produced result file before deciding",
                )
            if self._is_current_price_prompt(prompt):
                if not self._has_successful_price_lookup(tool_events):
                    if self._can_gracefully_fail_current_price_lookup(output, tool_events):
                        return VerificationResult("tool_expectation_v1", "pass", "")
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
            if "run_shell_command" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to inspect the local runtime with `run_shell_command`",
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
            ) and "run_shell_command" not in result_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to confirm runtime version or path claims with shell inspection commands before answering",
                )
        if route.task_signature in {"research/live_compare/general", "site/understanding/general"}:
            minimum_items = requested_minimum_list_items(prompt)
            if not used_tools:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to gather live evidence with web or browser tools, but no tools were used",
                )
            live_research_names = result_names & {
                "search_web",
                "fetch_url",
                "browser_render_page",
                "browser_screenshot",
                "agent_browser",
            }
            if not live_research_names:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to use at least one live research tool before answering",
                )
            if any(phrase in lowered for phrase in self.LIVE_RESEARCH_DISCOVERY_PHRASES) and len(successful_names) < 2:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to take at least two live research steps before answering this request",
                )
            if minimum_items > 0:
                if len(successful_names) < 3:
                    return VerificationResult(
                        "tool_expectation_v1",
                        "fail",
                        f"Expected Rocky to keep researching until it had enough grounded evidence for at least {minimum_items} items before answering",
                    )
                if not (result_names & {"fetch_url", "browser_render_page", "agent_browser"}):
                    return VerificationResult(
                        "tool_expectation_v1",
                        "fail",
                        "Expected Rocky to open or inspect a live listing/source page before assembling a counted live-research list",
                    )
            if any(
                phrase in lowered
                for phrase in ("tell me about", "who is ", "who are ", "biography", "biographies", "leader", "leaders", "member", "members")
            ) and not (result_names & {"fetch_url", "browser_render_page", "agent_browser"}):
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to open at least one retrieved live source before making people or role claims",
                )
        if route.task_signature == "data/spreadsheet/analysis":
            if not (result_names & {"run_shell_command", "read_file"}):
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to inspect the named CSV/XLSX file with `run_shell_command` or `read_file` before answering",
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
            if needs_follow_up_range and len(successful_names) < 2:
                return VerificationResult(
                    "tool_expectation_v1",
                    "fail",
                    "Expected Rocky to follow spreadsheet inspection with another shell or read step for the requested detail",
                )
        if route.task_signature == "extract/general" and len(successful_names) < 2:
            return VerificationResult(
                "tool_expectation_v1",
                "fail",
                "Expected Rocky to use at least two extraction steps before answering",
            )
        if route.task_signature == "extract/general" and not (result_names & {"run_shell_command", "read_file"}):
            return VerificationResult(
                "tool_expectation_v1",
                "fail",
                "Expected Rocky to use `run_shell_command` or `read_file` for extraction work",
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

    def _tool_failure(
        self,
        prompt: str,
        route: RouteDecision,
        output: str,
        tool_events: list[dict],
    ) -> VerificationResult:
        failures = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and not event.get("success", True)
        ]
        if failures and self._recovered_after_tool_failures(tool_events):
            return VerificationResult("tool_failure_v1", "pass", "")
        if (
            failures
            and route.task_signature == "repo/shell_execution"
            and self._is_current_price_prompt(prompt)
            and not self._has_successful_price_lookup(tool_events)
            and self._can_gracefully_fail_current_price_lookup(output, tool_events)
        ):
            return VerificationResult("tool_failure_v1", "pass", "")
        if failures and route.task_signature in {"research/live_compare/general", "site/understanding/general"}:
            successful_live_names = {
                str(event.get("name", ""))
                for event in tool_events
                if event.get("type") == "tool_result" and event.get("success", True)
            }
            failure_names = {str(event.get("name", "")) for event in failures if event.get("name")}
            nonfatal_guard_codes = {
                "use_explicit_url_first",
                "use_fetch_url_before_browser",
                "browser_runtime_unavailable",
                "use_web_fallback_after_browser_failure",
                "reuse_previous_live_page_evidence",
            }
            if successful_live_names & {"fetch_url", "browser_render_page", "agent_browser"}:
                all_failures_are_recoverable = all(
                    self._tool_error_code(event) in nonfatal_guard_codes
                    or str(event.get("name", "")) in {"search_web", "browser_render_page", "browser_screenshot"}
                    for event in failures
                )
                if all_failures_are_recoverable:
                    return VerificationResult("tool_failure_v1", "pass", "")
            if successful_live_names & {"fetch_url", "browser_render_page", "agent_browser"} and failure_names <= {
                "search_web",
                "browser_render_page",
                "browser_screenshot",
            }:
                return VerificationResult("tool_failure_v1", "pass", "")
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
            if successful_names_after_failure & {"fetch_url", "browser_render_page", "agent_browser"}:
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
                and successful_names_after_failure & {"run_shell_command", "write_file", "read_file"}
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

    def _json_file_contract(
        self,
        prompt: str,
        route: RouteDecision,
        tool_events: list[dict],
    ) -> VerificationResult:
        if route.task_signature not in {"repo/shell_execution", "automation/general"}:
            return VerificationResult("json_file_contract_v1", "pass", "")
        lowered = prompt.lower()
        if "write" not in lowered or ".json" not in lowered or "read" not in lowered:
            return VerificationResult("json_file_contract_v1", "pass", "")
        required_top_level_keys, required_item_keys = self._prompt_required_json_keys(prompt)
        if not required_top_level_keys and not required_item_keys:
            return VerificationResult("json_file_contract_v1", "pass", "")
        read_payload = self._latest_read_json_payload(prompt, tool_events)
        if read_payload is None:
            return VerificationResult("json_file_contract_v1", "pass", "")
        path, payload = read_payload
        if required_top_level_keys:
            if not isinstance(payload, dict):
                return VerificationResult(
                    "json_file_contract_v1",
                    "fail",
                    f"Expected `{path}` to contain a JSON object with top-level keys {list(required_top_level_keys)}, but it is {type(payload).__name__}.",
                )
            missing_top_level = [key for key in required_top_level_keys if key not in payload]
            if missing_top_level:
                return VerificationResult(
                    "json_file_contract_v1",
                    "fail",
                    f"Expected `{path}` to include top-level keys {missing_top_level} from the prompt contract.",
                )
        ok, detail = self._contains_required_item_keys(payload, required_top_level_keys, required_item_keys)
        if not ok:
            return VerificationResult(
                "json_file_contract_v1",
                "fail",
                f"Expected `{path}` to preserve the requested item schema, but {detail}.",
            )
        return VerificationResult("json_file_contract_v1", "pass", "")

    def _non_empty_output(self, prompt: str, output: str) -> VerificationResult:
        if not prompt.strip():
            return VerificationResult("non_empty_output_v1", "pass", "")
        if output.strip():
            return VerificationResult("non_empty_output_v1", "pass", "")
        return VerificationResult(
            "non_empty_output_v1",
            "fail",
            "Assistant returned an empty final answer and did not address the user's request.",
            failure_class="empty_final_answer",
        )

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
                "browser_render_page",
                "browser_screenshot",
                "agent_browser",
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
