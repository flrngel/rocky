from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from rocky.core.context import ContextBuilder
from rocky.core.runtime_state import AnswerContractBuilder, EvidenceAccumulator, EvidenceGraph, ThreadRegistry
from rocky.core.messages import Message
from rocky.core.permissions import PermissionDenied
from rocky.core.router import Lane, RouteDecision, Router
from rocky.core.system_prompt import build_system_prompt
from rocky.core.verifiers import VerificationResult, VerifierRegistry
from rocky.learning.manager import LearningManager
from rocky.providers.base import ProviderResponse
from rocky.providers.registry import ProviderRegistry
from rocky.session.store import Session, SessionStore
from rocky.tools.registry import ToolRegistry
from rocky.util.text import extract_json_candidate, safe_json
from rocky.util.time import utc_iso


@dataclass(slots=True)
class AgentResponse:
    text: str
    route: RouteDecision
    verification: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionOptions:
    continue_session: bool = True
    freeze: bool = False
    session_seed: Session | None = None


class AgentCore:
    def __init__(
        self,
        router: Router,
        sessions: SessionStore,
        context_builder: ContextBuilder,
        tool_registry: ToolRegistry,
        provider_registry: ProviderRegistry,
        verifier_registry: VerifierRegistry,
        learning_manager: LearningManager,
        permissions,
        traces_dir: Path,
        meta_handler: Callable[[str], str],
        *,
        create_layout: bool = True,
    ) -> None:
        self.router = router
        self.sessions = sessions
        self.context_builder = context_builder
        self.tool_registry = tool_registry
        self.provider_registry = provider_registry
        self.verifier_registry = verifier_registry
        self.learning_manager = learning_manager
        self.permissions = permissions
        self.traces_dir = traces_dir
        if create_layout:
            self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.meta_handler = meta_handler
        self.last_prompt: str | None = None
        self.last_answer: str | None = None
        self.last_trace: dict[str, Any] | None = None
        self.last_context: dict[str, Any] | None = None
        self.answer_contract_builder = AnswerContractBuilder()
        self.evidence_accumulator = EvidenceAccumulator()

    def _wants_prior_turn_context(self, prompt: str) -> bool:
        lowered = prompt.lower()
        return any(
            phrase in lowered
            for phrase in (
                "previous question",
                "previous message",
                "previous prompt",
                "what did i just ask",
                "what was my last question",
                "what did you just say",
                "what was my earlier question",
            )
        )

    def _trace_path(self) -> Path:
        stamp = utc_iso().replace(":", "").replace("-", "")
        return self.traces_dir / f"trace_{stamp}.json"

    def _write_trace(self, trace: dict[str, Any]) -> str:
        trace_path = self._trace_path()
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(safe_json(trace) + "\n", encoding="utf-8")
        return str(trace_path)

    def _session_title(self, prompt: str) -> str:
        head = " ".join(prompt.strip().split())
        return head[:60] or "session"

    def _error_response(
        self,
        session,
        prompt: str,
        route: RouteDecision,
        context_summary: dict[str, Any],
        selected_tools: list[str],
        selected_skills: list[str],
        provider_name: str,
        exc: Exception,
        options: ExecutionOptions,
        stream: bool = False,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentResponse:
        text = (
            f"Provider request failed: {exc}\n\n"
            "Rocky did not complete the task. Check the provider/base URL/model settings and try again."
        )
        if stream and event_handler:
            event_handler({"type": "assistant_chunk", "text": text})
        verification = {
            "name": "provider_failure_v1",
            "status": "fail",
            "message": str(exc),
        }
        trace = {
            "route": asdict(route),
            "selected_tools": selected_tools,
            "selected_skills": selected_skills,
            "provider": provider_name,
            "verification": verification,
            "tool_events": [],
            "context": context_summary,
            "error": {"type": exc.__class__.__name__, "message": str(exc)},
        }
        return self._finalize(session, prompt, text, route, verification, {}, trace, options=options)

    def _run_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        try:
            result = self.tool_registry.run(name, arguments)
        except PermissionDenied as exc:
            payload = {
                "success": False,
                "summary": str(exc),
                "data": {},
                "metadata": {"error": "permission_denied"},
            }
            text = safe_json(payload)
            if event_handler:
                event_handler({"type": "tool_result", "name": name, "text": text, "success": False})
            return text
        except Exception as exc:  # pragma: no cover - defensive runtime catch
            payload = {
                "success": False,
                "summary": f"Tool crashed: {exc}",
                "data": {},
                "metadata": {"error": "tool_exception"},
            }
            text = safe_json(payload)
            if event_handler:
                event_handler({"type": "tool_result", "name": name, "text": text, "success": False})
            return text
        text = result.as_text(limit=self.tool_registry.context.config.tools.max_tool_output_chars)
        if event_handler:
            event_handler({"type": "tool_result", "name": name, "text": text, "success": result.success})
        return text

    def _run_selected_tool(
        self,
        allowed_names: set[str],
        name: str,
        arguments: dict[str, Any],
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        if name not in allowed_names:
            payload = {
                "success": False,
                "summary": f"Tool not available for this task: {name}",
                "data": {},
                "metadata": {"error": "tool_not_exposed"},
            }
            text = safe_json(payload)
            if event_handler:
                event_handler({"type": "tool_result", "name": name, "text": text, "success": False})
            return text
        return self._run_tool(name, arguments, event_handler=event_handler)

    def _finalize(
        self,
        session,
        prompt: str,
        text: str,
        route: RouteDecision,
        verification: dict[str, Any],
        usage: dict[str, Any],
        trace: dict[str, Any],
        *,
        options: ExecutionOptions,
    ) -> AgentResponse:
        session.append("user", prompt)
        session.append("assistant", text)
        self.last_prompt = prompt
        self.last_answer = text
        self.last_trace = trace
        if not options.freeze:
            self.sessions.save(session)
            trace["trace_path"] = self._write_trace(trace)
            if route.lane != Lane.META:
                self.sessions.record_turn(
                    session,
                    prompt=prompt,
                    answer=text,
                    task_signature=route.task_signature,
                    verification=verification,
                    trace=trace,
                    execution_cwd=self.tool_registry.context.execution_relative,
                )
            try:
                self.learning_manager.record_query(
                    task_signature=route.task_signature,
                    skills_used=trace.get("selected_skills") or [],
                    verifier=verification.get("name", "default_v1"),
                    result="success" if verification.get("status") == "pass" else verification.get("status", "warn"),
                    usage=usage,
                    latency_ms=None,
                )
            except Exception:
                pass
        return AgentResponse(text=text, route=route, verification=verification, usage=usage, trace=trace)

    def _session_for_run(self, prompt: str, options: ExecutionOptions):
        if options.freeze:
            if options.session_seed is not None:
                return options.session_seed.clone()
            if options.continue_session:
                current = self.sessions.peek_current()
                if current is not None:
                    return current
            return self.sessions.create_ephemeral(title=self._session_title(prompt))
        if options.continue_session:
            return self.sessions.ensure_current()
        return self.sessions.create(title=self._session_title(prompt), make_current=False)

    def _successful_shell_observations(
        self,
        tool_events: list[dict[str, Any]] | None,
    ) -> list[tuple[str, str]]:
        if not tool_events:
            return []
        observations: list[tuple[str, str]] = []
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if event.get("name") != "run_shell_command":
                continue
            try:
                payload = json.loads(str(event.get("text", "")))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            command = str(data.get("command", "")).strip()
            if not command:
                arguments = event.get("arguments") or {}
                command = str(arguments.get("command", "")).strip()
            stdout = str(data.get("stdout", "")).strip()
            if command and stdout:
                observations.append((command, stdout))
        return observations

    def _latest_successful_shell_observation(
        self,
        tool_events: list[dict[str, Any]] | None,
    ) -> tuple[str, str] | None:
        observations = self._successful_shell_observations(tool_events)
        return observations[-1] if observations else None

    def _latest_successful_shell_stdout(
        self,
        tool_events: list[dict[str, Any]] | None,
        predicate: Callable[[str], bool],
    ) -> str:
        for command, stdout in reversed(self._successful_shell_observations(tool_events)):
            if predicate(command):
                return stdout.strip()
        return ""

    def _latest_successful_path(
        self,
        tool_events: list[dict[str, Any]] | None,
        tool_name: str,
    ) -> str:
        if not tool_events:
            return ""
        for event in reversed(tool_events):
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if event.get("name") != tool_name:
                continue
            arguments = event.get("arguments") or {}
            path = str(arguments.get("path", "")).strip()
            if not path:
                try:
                    payload = json.loads(str(event.get("text", "")))
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    data = payload.get("data")
                    metadata = payload.get("metadata")
                    if isinstance(data, dict):
                        path = str(data.get("path", "")).strip()
                    if not path and isinstance(metadata, dict):
                        path = str(metadata.get("path", "")).strip()
            if not path:
                continue
            if self.verifier_registry._is_internal_path(path):
                continue
            return path
        return ""

    def _price_subject_from_prompt(self, prompt: str) -> str:
        patterns = (
            r"check\s+the\s+([a-z0-9 .&/_-]+?)\s+(?:stock(?:'s)?\s+)?price",
            r"price\s+of\s+([a-z0-9 .&/_-]+?)(?:\s+of\s+today|\s+today|\s+current|\s+latest|$)",
            r"quote\s+for\s+([a-z0-9 .&/_-]+?)(?:\s+today|\s+current|\s+latest|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, prompt, flags=re.I)
            if not match:
                continue
            subject = re.sub(r"^(the|a|an)\s+", "", match.group(1).strip(), flags=re.I)
            subject = re.sub(r"\s+", " ", subject).strip(" .,:;")
            if not subject:
                continue
            if subject.isupper() and len(subject) <= 6:
                return subject
            return subject.title()
        return "Requested"

    def _normalize_output(
        self,
        route: RouteDecision,
        text: str,
        prompt: str = "",
        tool_events: list[dict[str, Any]] | None = None,
    ) -> str:
        lowered_prompt = prompt.lower()
        wants_exact_output = any(
            phrase in lowered_prompt
            for phrase in (
                "exact output",
                "exact json output",
                "tell me the exact output",
                "tell me the exact json output",
            )
        )
        wants_json_only = (
            "json" in lowered_prompt
            and any(
                phrase in lowered_prompt
                for phrase in (
                    "exact json",
                    "valid json",
                    "json only",
                    "prints valid json",
                    "tell me the exact json output",
                )
            )
        )
        if route.task_signature == "repo/shell_execution" and tool_events:
            if self.verifier_registry._is_current_price_prompt(prompt) and not self.verifier_registry._has_successful_price_lookup(tool_events):
                if (
                    len(self.verifier_registry._live_cli_source_hosts(tool_events)) >= 2
                    and len(self.verifier_registry._live_cli_source_attempts(tool_events)) >= 3
                ):
                    date_text = self._latest_successful_shell_stdout(
                        tool_events,
                        lambda command: command.strip().lower() == "date",
                    )
                    subject = self._price_subject_from_prompt(prompt)
                    lines: list[str] = []
                    if date_text:
                        lines.append(f"**Date today:** {date_text}")
                    lines.append(
                        f"**{subject} stock price:** Could not retrieve the live quote from multiple CLI sources in this environment."
                    )
                    return "\n\n".join(lines)
        if wants_json_only and route.task_signature == "repo/shell_execution":
            candidate = extract_json_candidate(text)
            output_path = self._latest_successful_path(tool_events, "read_file")
            if candidate and output_path.lower().endswith(".json") and any(
                phrase in lowered_prompt
                for phrase in (
                    "read the file back",
                    "read that file back",
                    "tell me the exact json",
                    "write valid json to",
                )
            ):
                try:
                    payload = json.loads(candidate)
                except Exception:
                    payload = None
                if payload is not None:
                    return safe_json(
                        {
                            "output_path": output_path,
                            "verified_output": payload,
                        }
                    )
        if wants_json_only and route.task_signature == "automation/general":
            observation = self._latest_successful_shell_observation(tool_events)
            if observation is not None:
                command, stdout = observation
                candidate = extract_json_candidate(stdout)
                if candidate:
                    try:
                        payload = json.loads(candidate)
                    except Exception:
                        payload = None
                    if payload is not None:
                        return safe_json(
                            {
                                "verified_command": command,
                                "verified_output": payload,
                            }
                        )
        if wants_exact_output and route.task_signature == "automation/general":
            observation = self._latest_successful_shell_observation(tool_events)
            if observation is not None:
                command, stdout = observation
                observed_line = stdout.strip().splitlines()[0].strip()
                if observed_line:
                    return f"Ran `{command}` and it printed `{observed_line}`."
        if route.task_signature == "extract/general" or wants_json_only:
            candidate = extract_json_candidate(text)
            if candidate:
                return candidate
        return text

    def _is_retryable_provider_exception(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500
        return isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ),
        )

    def _repair_structured_output(
        self,
        provider,
        system_prompt: str,
        prompt: str,
        route: RouteDecision,
        text: str,
        tool_events: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> str:
        if route.task_signature != "extract/general" or stream:
            return text
        if extract_json_candidate(text):
            return text
        successful_results = [
            event
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]
        if not successful_results:
            return text
        evidence_chunks: list[str] = []
        for event in successful_results[-4:]:
            payload = str(event.get("text", "")).strip()
            if payload:
                evidence_chunks.append(f"Tool `{event.get('name', 'unknown')}` output:\n{payload[:4000]}")
        if not evidence_chunks:
            return text
        repair_prompt = (
            f"Original task:\n{prompt}\n\n"
            f"Assistant draft that must be converted to JSON only:\n{text}\n\n"
            "Relevant tool outputs:\n"
            + "\n\n".join(evidence_chunks)
            + "\n\nReturn the final answer as valid JSON only. Do not add prose or markdown."
        )
        try:
            repair_response = provider.complete(
                system_prompt=system_prompt + "\n\nReturn valid JSON only with no prose or markdown.",
                messages=[Message(role="user", content=repair_prompt)],
                stream=False,
                event_handler=None,
            )
        except Exception:
            return text
        candidate = extract_json_candidate(repair_response.text)
        return candidate or text

    def _tool_loop_rounds(self, route: RouteDecision) -> int:
        if route.task_signature == "automation/general":
            return 12
        if route.lane == Lane.DEEP:
            return 10
        if route.task_signature in {"extract/general", "data/spreadsheet/analysis"}:
            return 8
        return 8

    def _verification_repair_prompt(
        self,
        prompt: str,
        route: RouteDecision,
        verification_message: str,
    ) -> str:
        route_hint = "Use more tools if needed before answering."
        if route.task_signature == "repo/shell_execution":
            route_hint = (
                "Do the execution first, then use separate follow-up inspection steps "
                "to verify or summarize the result instead of bundling everything into one tool call."
            )
            if any(phrase in prompt.lower() for phrase in ("explore the response", "analyze the response", "inspect the response", "candidates to merge", "candidate", "merge")):
                route_hint = (
                    "Execute the referenced workspace command or script first. Then use a separate follow-up parsing "
                    "step, such as `run_python`, to inspect the observed response before making decisions. If the "
                    "user asked for a result file, create it from the observed data and reread or verify it before "
                    "answering. If the user did not ask for a file, keep the decision output in the final answer "
                    "instead of creating new files. If the script is not executable or returns permission denied, "
                    "rerun it through an interpreter such as `sh x.sh` or `python3 tool.py`. If the live response "
                    "returns an auth, permission, network, or other error payload, report that you cannot make the "
                    "requested decision from live evidence instead of using previous traces or memories as a "
                    "substitute. Do not stop after only printing or paraphrasing the raw command output. Do not stay "
                    "in shell-only loops; make the non-shell follow-up parsing or file-inspection step within your "
                    "next few successful tool calls."
                )
            lowered = prompt.lower()
            if any(term in lowered for term in ("price", "stock", "quote")) and any(
                term in lowered for term in ("today", "current", "latest")
            ):
                route_hint = (
                    "Use shell commands to retrieve the exact current facts now. Interpret a company-name "
                    "price request as the company's stock quote unless the user explicitly asked for a product price. "
                    "If a live quote lookup fails, is rate-limited, or returns non-parseable output, retry with "
                    "a different CLI-accessible machine-readable source such as a plain CSV quote endpoint before answering. "
                    "Quote URLs that contain `?` or `&` so zsh does not misparse them. If several distinct live sources "
                    "still fail because of auth, rate limits, or network errors, say clearly that you could not retrieve "
                    "the live quote from this environment instead of inventing one."
                )
        elif route.task_signature == "data/spreadsheet/analysis":
            route_hint = (
                "Use more than one spreadsheet-analysis step. After `inspect_spreadsheet`, "
                "follow up with `read_sheet_range` or `run_python` before answering."
            )
        elif route.task_signature == "extract/general":
            route_hint = (
                "Use at least two extraction steps: inspect or discover the source, then parse or classify it, "
                "and return the final JSON only."
            )
        elif route.task_signature == "local/runtime_inspection":
            route_hint = (
                "Start with `inspect_runtime_versions`, then confirm paths or versions with a shell inspection step "
                "before answering."
            )
        elif route.task_signature == "automation/general":
            route_hint = (
                "Stop probing and move into implementation. If the task is to build, scaffold, or create an "
                "automation, do at most one lightweight inspection first, then use `write_file` within your next "
                "successful tool call or two. After the file exists, verify it with `run_shell_command`. Compare "
                "the verified command output against the user's requested behavior, sample data, and any required "
                "JSON shape. Do not use shell redirection, heredocs, `tee`, or inline interpreter file-writes as a "
                "substitute for `write_file` when creating project files. In the final answer, name the exact script "
                "or command you verified and include the exact observed output. If the observed output is wrong or "
                "incomplete, edit the files and rerun verification until the observed output matches. Use at least "
                "three successful tool steps for the finished automation flow: `write_file`, then `read_file` to "
                "reread the created script, then `run_shell_command` to execute it."
            )
        return (
            f"Original task:\n{prompt}\n\n"
            f"Your previous attempt did not pass verification:\n{verification_message}\n\n"
            f"{route_hint}\n"
            "Continue the task now, use more tools if needed, and return the corrected final answer."
        )

    def _automation_shell_write_guard(
        self,
        route: RouteDecision,
        prompt: str,
        name: str,
        arguments: dict[str, Any],
        successful_tool_names: list[str],
    ) -> str | None:
        if route.task_signature != "automation/general" or name != "run_shell_command":
            return None
        if "write_file" in successful_tool_names:
            return None
        lowered_prompt = prompt.lower()
        if not any(term in lowered_prompt for term in ("build", "create", "script", "scaffold", "project", "automation")):
            return None
        command = str(arguments.get("command") or "").strip()
        lowered_command = command.lower()
        if not successful_tool_names and self._is_lightweight_automation_inspection_command(lowered_command):
            return None
        payload = {
            "success": False,
            "summary": (
                "Use `write_file` to create or edit project files before shell verification. "
                "Only one lightweight inspection command is allowed before the first `write_file`; "
                "do not use repeated shell setup or shell-based file creation as a substitute."
            ),
            "data": {},
            "metadata": {"error": "use_write_file_first"},
        }
        return safe_json(payload)

    def _shell_follow_up_guard(
        self,
        route: RouteDecision,
        prompt: str,
        name: str,
        successful_tool_names: list[str],
    ) -> str | None:
        if route.task_signature != "repo/shell_execution" or name != "run_shell_command":
            return None
        lowered_prompt = prompt.lower()
        needs_response_analysis = any(
            phrase in lowered_prompt
            for phrase in (
                "explore the response",
                "analyze the response",
                "inspect the response",
                "response",
                "decide",
                "classify",
                "candidate",
                "merge",
            )
        )
        if not needs_response_analysis:
            return None
        if "run_shell_command" not in successful_tool_names:
            return None
        if any(tool_name != "run_shell_command" for tool_name in successful_tool_names):
            return None
        payload = {
            "success": False,
            "summary": (
                "The command already ran successfully. Use a non-shell follow-up step now, such as `run_python`, "
                "`read_file`, `stat_path`, or `write_file`, to inspect or structure the observed result before "
                "making a decision."
            ),
            "data": {},
            "metadata": {"error": "use_non_shell_follow_up"},
        }
        return safe_json(payload)

    def _is_lightweight_automation_inspection_command(self, command: str) -> bool:
        if not command:
            return False
        blocked_markers = ("&&", "||", ";", ">", "<", "|", "tee ", "mkdir ", "cp ", "mv ", "rm ", "touch ", "chmod ")
        if any(marker in command for marker in blocked_markers):
            return False
        prefixes = (
            "pwd",
            "ls",
            "find ",
            "stat ",
            "cat ",
            "head ",
            "wc ",
            "git status",
            "git branch",
            "git rev-parse",
            "which ",
            "command -v ",
            "env",
            "printenv",
            "test -f ",
            "test -d ",
        )
        return command.startswith(prefixes)

    def _should_judge_automation_output(
        self,
        route: RouteDecision,
        prompt: str,
        tool_events: list[dict[str, Any]],
        *,
        stream: bool,
    ) -> bool:
        if stream:
            return False
        lowered = prompt.lower()
        if not any(
            phrase in lowered
            for phrase in (
                "exact output",
                "exact json output",
                "valid json output",
                "prints valid json",
            )
        ):
            return False
        if route.task_signature == "automation/general":
            return any(
                event.get("type") == "tool_result"
                and event.get("name") == "run_shell_command"
                and event.get("success", True)
                for event in tool_events
            )
        if route.task_signature == "repo/shell_execution":
            return any(
                event.get("type") == "tool_result"
                and event.get("success", True)
                and event.get("name") in {"run_shell_command", "read_file"}
                for event in tool_events
            )
        return False

    def _judge_automation_output(
        self,
        provider,
        prompt: str,
        tool_events: list[dict[str, Any]],
    ) -> VerificationResult:
        relevant_results = [
            event
            for event in tool_events
            if event.get("type") == "tool_result"
            and event.get("success", True)
            and event.get("name") in {"run_shell_command", "read_file"}
        ]
        if not relevant_results:
            return VerificationResult("automation_output_judge_v1", "pass", "")
        evidence = "\n\n".join(
            f"{event.get('name', 'tool_result')} {index}:\n{event.get('text', '')}"
            for index, event in enumerate(relevant_results[-4:], start=1)
        )
        judge_prompt = (
            f"Original task:\n{prompt}\n\n"
            "Observed successful verification output(s):\n"
            f"{evidence}\n\n"
            "Decide whether the observed output satisfies the task exactly. "
            "Use any explicit sample data, requested calculations, required JSON shape, and required array contents from the task. "
            "Return JSON only in the form "
            '{"status":"pass"|"fail","reason":"short reason"}'
            ". Mark it fail when the observed output clearly contradicts the task."
        )
        try:
            response = provider.complete(
                system_prompt="You verify whether a built local project's observed output satisfies the user's task exactly. Return JSON only.",
                messages=[Message(role="user", content=judge_prompt)],
                stream=False,
                event_handler=None,
            )
        except Exception:
            return VerificationResult("automation_output_judge_v1", "pass", "")
        candidate = extract_json_candidate(response.text)
        if not candidate:
            return VerificationResult("automation_output_judge_v1", "pass", "")
        try:
            payload = json.loads(candidate)
        except Exception:
            return VerificationResult("automation_output_judge_v1", "pass", "")
        status = str(payload.get("status", "pass")).lower()
        reason = str(payload.get("reason", "")).strip()
        if status == "fail":
            return VerificationResult(
                "automation_output_judge_v1",
                "fail",
                reason or "Observed automation output did not satisfy the task exactly",
            )
        return VerificationResult("automation_output_judge_v1", "pass", reason)

    def _retry_after_verification_failure(
        self,
        provider,
        system_prompt: str,
        messages: list[Message],
        route: RouteDecision,
        selected_tool_objects: list[Any],
        selected_tool_names: set[str],
        verification_message: str,
        normalized_text: str,
        prompt: str,
        *,
        stream: bool,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ):
        successful_tool_names: list[str] = []

        def execute_tool(name: str, arguments: dict[str, Any]) -> str:
            guarded = self._automation_shell_write_guard(
                route,
                prompt,
                name,
                arguments,
                successful_tool_names,
            )
            if guarded is not None:
                if event_handler:
                    event_handler({"type": "tool_result", "name": name, "text": guarded, "success": False})
                return guarded
            guarded = self._shell_follow_up_guard(
                route,
                prompt,
                name,
                successful_tool_names,
            )
            if guarded is not None:
                if event_handler:
                    event_handler({"type": "tool_result", "name": name, "text": guarded, "success": False})
                return guarded
            text = self._run_selected_tool(
                selected_tool_names,
                name,
                arguments,
                event_handler=event_handler,
            )
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            if payload.get("success", False):
                successful_tool_names.append(name)
            return text

        retry_messages = [
            *messages,
            Message(role="assistant", content=normalized_text),
            Message(
                role="user",
                content=self._verification_repair_prompt(
                    messages[-1].content if messages else "",
                    route,
                    verification_message,
                ),
            ),
        ]
        return provider.run_with_tools(
            system_prompt=system_prompt,
            messages=retry_messages,
            tools=[tool.openai_schema() for tool in selected_tool_objects],
            execute_tool=execute_tool,
            max_rounds=self._tool_loop_rounds(route),
            event_handler=event_handler if stream else None,
        )

    def _sync_thread_from_evidence(self, thread, evidence_graph) -> None:
        thread.artifact_refs = [str(item.get("ref")) for item in evidence_graph.artifacts[:16] if item.get("ref")]
        thread.entity_refs = [str(item.get("value")) for item in evidence_graph.entities[:16] if item.get("value")]
        thread.claims = [claim.claim_id for claim in evidence_graph.claims]
        thread.unresolved_questions = [str(item.get("text")) for item in evidence_graph.questions[:8] if item.get("text")]
        thread.touch()

    def _supported_claim_records(self, evidence_graph, answer_contract) -> list[dict[str, Any]]:
        allowed = set(answer_contract.allowed_claim_ids if answer_contract else [])
        claims = []
        for claim in evidence_graph.claims:
            if claim.status not in {"active", "provisional"}:
                continue
            if allowed and claim.claim_id not in allowed:
                continue
            claims.append(claim.as_record())
        return claims[:24]

    def _verification_payload(self, result: VerificationResult) -> dict[str, Any]:
        return result.as_record()

    def run(
        self,
        prompt: str,
        stream: bool = False,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
        continue_session: bool = True,
        freeze: bool = False,
        session_seed: Session | None = None,
    ) -> AgentResponse:
        options = ExecutionOptions(
            continue_session=continue_session,
            freeze=freeze,
            session_seed=session_seed,
        )
        session = self._session_for_run(prompt, options)
        thread_registry = ThreadRegistry(session)
        workspace_root = str(self.tool_registry.context.workspace_root)
        execution_cwd = self.tool_registry.context.execution_relative
        route, continuation = self.router.resolve(
            prompt,
            active_threads=thread_registry.active_threads(),
            recent_threads=thread_registry.recent_threads(),
            workspace_root=workspace_root,
            execution_cwd=execution_cwd,
        )
        if route.lane == Lane.META:
            text = self.meta_handler(prompt)
            if stream and event_handler and text:
                event_handler({"type": "assistant_chunk", "text": text})
            verification_result = VerificationResult("meta_v1", "pass", "Deterministic meta answer", memory_promotion_allowed=False, learning_promotion_allowed=False)
            verification = self._verification_payload(verification_result)
            trace = {
                "route": asdict(route),
                "continuation": {
                    "action": continuation.action,
                    "thread_id": continuation.thread_id,
                    "confidence": continuation.confidence,
                    "score": continuation.score,
                    "reasons": continuation.reasons,
                },
                "selected_tools": [],
                "selected_skills": [],
                "provider": "deterministic",
                "verification": verification,
                "tool_events": [],
                "context": {"instructions": [], "memories": [], "skills": [], "tool_families": []},
                "thread": thread_registry.snapshot(),
            }
            self.last_context = trace["context"]
            return self._finalize(session, prompt, text, route, verification, {}, trace, options=options)

        task_family = route.task_class.value if hasattr(route.task_class, 'value') else str(route.task_class)
        active_thread = thread_registry.ensure_thread(
            route_task_signature=route.task_signature,
            task_family=task_family,
            workspace_root=workspace_root,
            execution_cwd=execution_cwd,
            continued_thread_id=route.continued_thread_id,
        )
        evidence_graph = thread_registry.evidence.get(active_thread.thread_id)
        if evidence_graph is None:
            evidence_graph = EvidenceGraph(thread_id=active_thread.thread_id)
            thread_registry.evidence[active_thread.thread_id] = evidence_graph
        active_thread.status = 'active'
        active_thread.add_prompt(prompt)
        active_thread.add_route(asdict(route))
        self.evidence_accumulator.ingest_prompt(evidence_graph, prompt)
        self._sync_thread_from_evidence(active_thread, evidence_graph)

        pre_answer_contract = self.answer_contract_builder.build(
            prompt,
            route.task_signature,
            active_thread,
            evidence_graph,
            prior_answer=self.last_answer,
        )
        context = self.context_builder.build(
            prompt,
            route.task_signature,
            route.tool_families,
            current_session_id=session.id,
            active_thread=active_thread,
            evidence_graph=evidence_graph,
            answer_contract=pre_answer_contract,
        )
        context_summary = context.summary()
        self.last_context = context_summary
        system_prompt = build_system_prompt(
            context,
            self.permissions.config.mode,
            prompt,
            route.task_signature,
        )
        recent_messages = session.recent_messages(limit=12) if continue_session else []
        if not recent_messages and self._wants_prior_turn_context(prompt):
            text = "I don't have any earlier turn context in this run, so I can't tell what your previous question was."
            if stream and event_handler:
                event_handler({"type": "assistant_chunk", "text": text})
            verification_result = VerificationResult(
                "context_boundary_v1",
                "pass",
                "Answered from the available conversation boundary",
                memory_promotion_allowed=False,
                learning_promotion_allowed=False,
            )
            verification = self._verification_payload(verification_result)
            active_thread.add_answer(text)
            active_thread.add_verification(verification)
            thread_registry.save()
            trace = {
                "route": asdict(route),
                "continuation": {
                    "action": continuation.action,
                    "thread_id": continuation.thread_id,
                    "confidence": continuation.confidence,
                    "score": continuation.score,
                    "reasons": continuation.reasons,
                },
                "selected_tools": [],
                "selected_skills": [],
                "provider": "deterministic",
                "verification": verification,
                "tool_events": [],
                "context": context_summary,
                "thread": thread_registry.snapshot(active_thread.thread_id),
                "answer_contract": pre_answer_contract.as_record(),
                "supported_claims": self._supported_claim_records(evidence_graph, pre_answer_contract),
            }
            return self._finalize(session, prompt, text, route, verification, {}, trace, options=options)
        messages = [*recent_messages, Message(role="user", content=prompt)]
        provider = self.provider_registry.provider_for_task(needs_tools=bool(route.tool_families))
        selected_tool_objects = self.tool_registry.select_for_task(
            route.tool_families,
            route.task_signature,
            prompt,
        )
        selected_tools = [tool.name for tool in selected_tool_objects]
        selected_tool_names = set(selected_tools)
        selected_skills = [item["name"] for item in context.skills]
        provider_name = provider.__class__.__name__

        provider_response = None
        attempts = 3
        for attempt in range(attempts):
            successful_tool_names: list[str] = []

            def execute_tool(name: str, arguments: dict[str, Any]) -> str:
                guarded = self._automation_shell_write_guard(
                    route,
                    prompt,
                    name,
                    arguments,
                    successful_tool_names,
                )
                if guarded is not None:
                    if event_handler:
                        event_handler({"type": "tool_result", "name": name, "text": guarded, "success": False})
                    return guarded
                guarded = self._shell_follow_up_guard(
                    route,
                    prompt,
                    name,
                    successful_tool_names,
                )
                if guarded is not None:
                    if event_handler:
                        event_handler({"type": "tool_result", "name": name, "text": guarded, "success": False})
                    return guarded
                text = self._run_selected_tool(
                    selected_tool_names,
                    name,
                    arguments,
                    event_handler=event_handler,
                )
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = {}
                if payload.get("success", False):
                    successful_tool_names.append(name)
                return text

            try:
                if route.tool_families:
                    provider_response = provider.run_with_tools(
                        system_prompt=system_prompt,
                        messages=messages,
                        tools=[tool.openai_schema() for tool in selected_tool_objects],
                        execute_tool=execute_tool,
                        max_rounds=self._tool_loop_rounds(route),
                        event_handler=event_handler if stream else None,
                    )
                else:
                    provider_response = provider.complete(
                        system_prompt=system_prompt,
                        messages=messages,
                        stream=stream,
                        event_handler=event_handler,
                    )
                break
            except Exception as exc:
                if attempt < attempts - 1 and self._is_retryable_provider_exception(exc):
                    time.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                trace = {
                    "route": asdict(route),
                    "continuation": {
                        "action": continuation.action,
                        "thread_id": continuation.thread_id,
                        "confidence": continuation.confidence,
                        "score": continuation.score,
                        "reasons": continuation.reasons,
                    },
                    "selected_tools": selected_tools,
                    "selected_skills": selected_skills,
                    "provider": provider_name,
                    "verification": {"name": "provider_failure_v1", "status": "fail", "message": str(exc)},
                    "tool_events": [],
                    "context": context_summary,
                    "thread": thread_registry.snapshot(active_thread.thread_id),
                    "answer_contract": pre_answer_contract.as_record(),
                }
                return self._error_response(
                    session=session,
                    prompt=prompt,
                    route=route,
                    context_summary=context_summary,
                    selected_tools=selected_tools,
                    selected_skills=selected_skills,
                    provider_name=provider_name,
                    exc=exc,
                    options=options,
                    stream=stream,
                    event_handler=event_handler,
                )
        assert provider_response is not None

        normalized_text = self._normalize_output(
            route,
            provider_response.text,
            prompt,
            provider_response.tool_events,
        )
        normalized_text = self._repair_structured_output(
            provider,
            system_prompt,
            prompt,
            route,
            normalized_text,
            provider_response.tool_events,
            stream=stream,
        )
        self.evidence_accumulator.ingest_tool_events(evidence_graph, provider_response.tool_events)
        active_thread.add_tool_events(provider_response.tool_events)
        self._sync_thread_from_evidence(active_thread, evidence_graph)
        answer_contract = self.answer_contract_builder.build(
            prompt,
            route.task_signature,
            active_thread,
            evidence_graph,
            prior_answer=self.last_answer,
        )
        verification_result = self.verifier_registry.verify(
            prompt=prompt,
            route=route,
            task_class=route.task_class,
            output=normalized_text,
            tool_events=provider_response.tool_events,
            active_thread=active_thread,
            evidence_graph=evidence_graph,
            answer_contract=answer_contract,
            prior_answer=self.last_answer,
            continuation_expected=continuation.action != 'start_new_thread',
        )
        if verification_result.status == "pass" and self._should_judge_automation_output(
            route,
            prompt,
            provider_response.tool_events,
            stream=stream,
        ):
            verification_result = self._judge_automation_output(
                provider,
                prompt,
                provider_response.tool_events,
            )
        if route.tool_families and verification_result.status == "fail":
            for _ in range(2):
                retry_response = self._retry_after_verification_failure(
                    provider,
                    system_prompt,
                    messages,
                    route,
                    selected_tool_objects,
                    selected_tool_names,
                    verification_result.message,
                    normalized_text,
                    prompt,
                    stream=stream,
                    event_handler=event_handler,
                )
                provider_response = ProviderResponse(
                    text=retry_response.text,
                    usage=retry_response.usage or provider_response.usage,
                    raw={
                        "initial": provider_response.raw,
                        "retry": retry_response.raw,
                    },
                    tool_events=[*provider_response.tool_events, *retry_response.tool_events],
                )
                normalized_text = self._normalize_output(
                    route,
                    provider_response.text,
                    prompt,
                    provider_response.tool_events,
                )
                normalized_text = self._repair_structured_output(
                    provider,
                    system_prompt,
                    prompt,
                    route,
                    normalized_text,
                    provider_response.tool_events,
                    stream=stream,
                )
                self.evidence_accumulator.ingest_tool_events(evidence_graph, retry_response.tool_events)
                active_thread.add_tool_events(retry_response.tool_events)
                self._sync_thread_from_evidence(active_thread, evidence_graph)
                answer_contract = self.answer_contract_builder.build(
                    prompt,
                    route.task_signature,
                    active_thread,
                    evidence_graph,
                    prior_answer=self.last_answer,
                )
                verification_result = self.verifier_registry.verify(
                    prompt=prompt,
                    route=route,
                    task_class=route.task_class,
                    output=normalized_text,
                    tool_events=provider_response.tool_events,
                    active_thread=active_thread,
                    evidence_graph=evidence_graph,
                    answer_contract=answer_contract,
                    prior_answer=self.last_answer,
                    continuation_expected=continuation.action != 'start_new_thread',
                )
                if verification_result.status == "pass" and self._should_judge_automation_output(
                    route,
                    prompt,
                    provider_response.tool_events,
                    stream=stream,
                ):
                    verification_result = self._judge_automation_output(
                        provider,
                        prompt,
                        provider_response.tool_events,
                    )
                if verification_result.status != "fail":
                    break

        verification = self._verification_payload(verification_result)
        active_thread.add_answer(normalized_text)
        active_thread.add_verification(verification)
        self._sync_thread_from_evidence(active_thread, evidence_graph)
        thread_registry.save()
        trace = {
            "route": asdict(route),
            "continuation": {
                "action": continuation.action,
                "thread_id": continuation.thread_id,
                "confidence": continuation.confidence,
                "score": continuation.score,
                "reasons": continuation.reasons,
            },
            "selected_tools": selected_tools,
            "selected_skills": selected_skills,
            "provider": provider_name,
            "verification": verification,
            "usage": provider_response.usage,
            "tool_events": provider_response.tool_events,
            "context": context_summary,
            "raw_provider_keys": sorted(provider_response.raw.keys()),
            "thread": thread_registry.snapshot(active_thread.thread_id),
            "answer_contract": answer_contract.as_record(),
            "supported_claims": self._supported_claim_records(evidence_graph, answer_contract),
        }
        current_thread = trace.get("thread", {}).get("current_thread") or {}
        if current_thread:
            current_thread["summary_text"] = active_thread.summary_text()
            trace["thread"]["current_thread"] = current_thread
        return self._finalize(
            session=session,
            prompt=prompt,
            text=normalized_text,
            route=route,
            verification=verification,
            usage=provider_response.usage,
            trace=trace,
            options=options,
        )
