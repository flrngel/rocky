from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from rocky.core.context import ContextBuilder
from rocky.core.research_synthesis import build_counted_research_list_answer
from rocky.core.run_flow import RunFlowManager
from rocky.core.runtime_state import (
    AnswerContractBuilder,
    EvidenceAccumulator,
    EvidenceGraph,
    ThreadRegistry,
    prompt_requests_list_output,
    requested_minimum_list_items,
)
from rocky.core.messages import Message
from rocky.core.router import Lane, RouteDecision, Router
from rocky.core.system_prompt import build_system_prompt
from rocky.core.verifiers import VerificationResult, VerifierRegistry
from rocky.learning.manager import LearningManager
from rocky.providers.base import ProviderResponse
from rocky.providers.registry import ProviderRegistry
from rocky.session.store import Session, SessionStore
from rocky.tool_events import (
    compact_tool_result_event,
    ensure_tool_result_event,
    tool_event_artifacts,
    tool_event_brief_for_prompt,
    tool_event_payload,
)
from rocky.tools.registry import ToolRegistry
from rocky.util.io import read_text
from rocky.util.text import extract_json_candidate, safe_json, tokenize_keywords
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
    _BACKCHANNEL_TOKENS = {
        "bye",
        "cool",
        "goodbye",
        "great",
        "hello",
        "hey",
        "hi",
        "kk",
        "nice",
        "no",
        "nope",
        "ok",
        "okay",
        "sure",
        "thanks",
        "thank",
        "thx",
        "yep",
        "yes",
    }
    _TASK_SIGNATURE_DEFAULTS = {
        "repo/*": "repo/general",
        "automation/*": "automation/general",
        "extract/*": "extract/general",
        "data/*": "data/spreadsheet/analysis",
        "local/*": "local/runtime_inspection",
        "research/*": "research/live_compare/general",
        "site/*": "site/understanding/general",
    }
    _TASK_SIGNATURE_BIAS = {
        "repo/shell_execution": 4,
        "automation/general": 3,
        "data/spreadsheet/analysis": 3,
        "extract/general": 2,
        "repo/general": 2,
        "repo/shell_inspection": 2,
        "local/runtime_inspection": 2,
        "research/live_compare/general": 1,
        "site/understanding/general": 1,
    }
    _SHELLISH_MARKERS = (
        "```bash",
        "available tools",
        "print json",
        "print output",
        "run_shell_command",
        "stdout",
        "uv run ",
        "workflow",
    )
    _RESEARCH_TOKEN_STOPWORDS = {
        "chat",
        "edit",
        "generation",
        "gguf",
        "image",
        "instruct",
        "it",
        "large",
        "mini",
        "mlx",
        "model",
        "small",
        "text",
        "thinking",
        "uncensored",
        "video",
    }

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

    def _looks_like_backchannel_prompt(self, prompt: str) -> bool:
        words = re.findall(r"[a-z0-9_.+-]+", prompt.lower())
        if not words or len(words) > 4:
            return False
        return all(word in self._BACKCHANNEL_TOKENS for word in words)

    def _looks_like_atomic_workspace_task(self, prompt: str) -> bool:
        text = prompt.strip()
        if not text or text.startswith("/") or len(text) > 80:
            return False
        if "\n" in text or "```" in text:
            return False
        if self._looks_like_backchannel_prompt(text):
            return False
        words = re.findall(r"[a-z0-9_.+-]+", text.lower())
        return 0 < len(words) <= 8

    def _resolve_project_task_signature(self, raw_signature: str) -> str | None:
        signature = raw_signature.strip()
        if not signature or signature == "conversation/general":
            return None
        if signature.endswith("*"):
            return self._TASK_SIGNATURE_DEFAULTS.get(signature)
        return signature

    def _preview_instruction_texts(self) -> list[str]:
        texts: list[str] = []
        for path in self.context_builder.instruction_candidates:
            if not path.exists():
                continue
            try:
                text = read_text(path)[:6000]
            except Exception:
                continue
            if text.strip():
                texts.append(text)
        return texts

    def _instruction_shell_bias(self, instruction_texts: list[str]) -> int:
        score = 0
        for text in instruction_texts:
            lowered = text.lower()
            score += sum(1 for marker in self._SHELLISH_MARKERS if marker in lowered)
            if "json array" in lowered or "valid json" in lowered:
                score += 2
            if "your job is to" in lowered or "given product name" in lowered or "for a given" in lowered:
                score += 1
        return min(score, 8)

    def _maybe_upgrade_route_from_project_context(
        self,
        prompt: str,
        route: RouteDecision,
    ) -> RouteDecision:
        if route.lane == Lane.META or route.tool_families:
            return route
        if not route.task_signature.startswith("conversation/"):
            return route
        if not self._looks_like_atomic_workspace_task(prompt):
            return route

        prompt_lower = prompt.lower()
        prompt_tokens = tokenize_keywords(prompt)
        instruction_texts = self._preview_instruction_texts()
        instruction_shell_bias = self._instruction_shell_bias(instruction_texts)
        retrieved_guidance = [
            *self.context_builder.skill_retriever.retrieve(prompt, route.task_signature, limit=6),
            *self.context_builder.policy_retriever.retrieve(prompt, route.task_signature, limit=6),
        ]
        best_candidate: tuple[int, str, str, str] | None = None

        for guidance in retrieved_guidance:
            guidance_kind = "policy" if getattr(guidance, "kind", "") == "learned_policy" else "skill"
            # Teach over-tagging guard (policies only): /teach auto-generates
            # policies declaring multiple task_signatures (conversation/general +
            # domain families) from a single correction. If the current route is
            # one of those declared AND other signatures are declared too, the
            # policy is ambiguously scoped — prefer the current route. Skills are
            # manually authored; their multi-signature declarations are intentional
            # (e.g., short-prompt → shell upgrade) and must not be gated here.
            if guidance_kind == "policy":
                declared_raw = [
                    str(item).strip()
                    for item in (guidance.task_signatures or [])
                    if str(item).strip()
                ]
                if route.task_signature in declared_raw and len(declared_raw) > 1:
                    continue
            candidate_signatures = [
                resolved
                for resolved in (self._resolve_project_task_signature(item) for item in guidance.task_signatures)
                if resolved is not None
            ]
            candidate_signatures.extend(
                signature
                for signature in self._infer_route_signatures_from_guidance(prompt, guidance, current_signature=route.task_signature)
                if signature not in candidate_signatures
            )
            if not candidate_signatures:
                continue

            guidance_text = guidance.body.lower()
            shellish_guidance = instruction_shell_bias >= 3 or any(marker in guidance_text for marker in self._SHELLISH_MARKERS)
            retrieval_tokens = tokenize_keywords(guidance.name) | tokenize_keywords(guidance.description)
            for trigger in guidance.triggers:
                retrieval_tokens |= tokenize_keywords(trigger)
            for keyword in guidance.retrieval_keywords:
                retrieval_tokens |= tokenize_keywords(keyword)
            overlap = prompt_tokens & retrieval_tokens

            guidance_score = 0
            if guidance.scope == "project":
                guidance_score += 4
            if guidance.origin in {"project", "project_bundled", "compat", "learned", "learned_legacy"}:
                guidance_score += 1
            if guidance_kind == "policy":
                guidance_score += 2
            if any(trigger.lower() in prompt_lower for trigger in guidance.triggers if trigger.strip()):
                guidance_score += 6
            guidance_score += min(len(overlap), 4) * 2
            if shellish_guidance:
                guidance_score += 2

            for signature in candidate_signatures:
                signature_score = guidance_score + self._TASK_SIGNATURE_BIAS.get(signature, 0)
                if signature == "repo/shell_execution" and not shellish_guidance:
                    signature_score -= 2
                if best_candidate is None or signature_score > best_candidate[0]:
                    best_candidate = (signature_score, signature, guidance.name, guidance_kind)

        if best_candidate is not None and best_candidate[0] >= 9:
            upgraded = self.router.decision_for_task_signature(
                best_candidate[1],
                reasoning=f"Project {best_candidate[3]} `{best_candidate[2]}` maps this short workspace prompt to {best_candidate[1]}",
                confidence=min(0.93, 0.55 + best_candidate[0] / 20),
                source="project_context",
            )
            if upgraded is not None:
                upgraded.continuation_decision = route.continuation_decision
                upgraded.continued_thread_id = route.continued_thread_id
                return upgraded

        if instruction_shell_bias >= 6:
            upgraded = self.router.decision_for_task_signature(
                "repo/shell_execution",
                reasoning="Project instructions define a shell-backed workflow for terse workspace inputs",
                confidence=0.76,
                source="project_context",
            )
            if upgraded is not None:
                upgraded.continuation_decision = route.continuation_decision
                upgraded.continued_thread_id = route.continued_thread_id
                return upgraded

        return route

    def _infer_route_signatures_from_guidance(
        self,
        prompt: str,
        guidance,
        *,
        current_signature: str,
    ) -> list[str]:
        metadata = guidance.metadata
        guidance_parts = [prompt.strip(), guidance.description.strip()]
        feedback_excerpt = str(metadata.get("feedback_excerpt") or "").strip()
        if feedback_excerpt:
            guidance_parts.append(feedback_excerpt)
        for key in ("required_behavior", "prohibited_behavior", "evidence_requirements"):
            guidance_parts.extend(
                str(item).strip()
                for item in (metadata.get(key) or [])
                if str(item).strip()
            )
        guidance_prompt = "\n".join(part for part in guidance_parts if part).strip()
        if not guidance_prompt:
            return []
        inferred = self.router.route(guidance_prompt)
        inferred_signature = self._resolve_project_task_signature(inferred.task_signature)
        if (
            inferred_signature is None
            or inferred_signature == current_signature
            or not inferred.tool_families
        ):
            return []
        return [inferred_signature]

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
        payload = dict(trace)
        payload["trace_path"] = str(trace_path)
        trace_path.write_text(safe_json(payload) + "\n", encoding="utf-8")
        trace["trace_path"] = str(trace_path)
        return str(trace_path)

    def _tool_event_storage_dir(self) -> Path:
        return self.traces_dir / "tool-results"

    def _prepare_tool_events(self, tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for event in tool_events:
            if event.get("type") != "tool_result":
                prepared.append(dict(event))
                continue
            normalized = ensure_tool_result_event(event)
            prepared.append(
                compact_tool_result_event(
                    normalized,
                    storage_dir=self._tool_event_storage_dir(),
                )
            )
        return prepared

    def _merge_usage(self, *payloads: dict[str, Any] | None) -> dict[str, int]:
        totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            prompt_tokens = int(payload.get("prompt_tokens", payload.get("input_tokens", 0)) or 0)
            completion_tokens = int(payload.get("completion_tokens", payload.get("output_tokens", 0)) or 0)
            total_tokens = int(payload.get("total_tokens", 0) or 0)
            if total_tokens <= 0:
                total_tokens = prompt_tokens + completion_tokens
            totals["prompt_tokens"] += max(0, prompt_tokens)
            totals["completion_tokens"] += max(0, completion_tokens)
            totals["total_tokens"] += max(0, total_tokens)
            request_count = int(payload.get("requests", 0) or 0)
            if request_count <= 0 and (prompt_tokens or completion_tokens or total_tokens):
                request_count = 1
            totals["requests"] += max(0, request_count)
        if totals["total_tokens"] <= 0:
            totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
        return totals

    def _should_use_flow_loop(self, route: RouteDecision) -> bool:
        if not route.tool_families:
            return False
        return not route.task_signature.startswith("conversation/")

    def _verification_needs_more_evidence(self, route: RouteDecision, result: VerificationResult) -> bool:
        if not route.task_signature.startswith(("research/", "site/")):
            return False
        failure_class = str(result.failure_class or "")
        if failure_class in {
            "answer_claimed_knowledge_without_reference",
            "minimum_list_count_not_met",
            "counted_list_missing_live_evidence",
            "counted_list_live_pages_too_shallow",
            "counted_list_search_stopped_too_early",
            "unsupported_claim_introduced",
        }:
            return True
        message = str(result.message or "").lower()
        return any(
            phrase in message
            for phrase in (
                "missing evidence",
                "unsupported",
                "gather more",
                "open more",
                "live item evidence",
                "stopped the counted",
                "too early",
                "tool failures observed",
            )
        )

    def _research_evidence_backed_answer(
        self,
        prompt: str,
        route: RouteDecision,
        tool_events: list[dict[str, Any]],
    ) -> str:
        return build_counted_research_list_answer(prompt, route.task_signature, tool_events)

    def _try_research_evidence_backed_answer(
        self,
        *,
        prompt: str,
        route: RouteDecision,
        active_thread,
        evidence_graph,
        tool_events: list[dict[str, Any]],
    ) -> tuple[str, Any, VerificationResult] | None:
        candidate_text = self._research_evidence_backed_answer(prompt, route, tool_events)
        if not candidate_text:
            return None
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
            output=candidate_text,
            tool_events=tool_events,
            active_thread=active_thread,
            evidence_graph=evidence_graph,
            answer_contract=answer_contract,
            prior_answer=self.last_answer,
            continuation_expected=False,
        )
        if verification_result.status != "pass":
            return None
        return candidate_text, answer_contract, verification_result

    def _system_prompt_with_flow(self, system_prompt: str, flow: RunFlowManager) -> str:
        return (
            f"{system_prompt}\n\n"
            "## Run flow\n"
            "You are working inside a run-local flow controller. Think from the flow and the active task note, not from broad transcript memory.\n"
            "Only the active task may drive the next burst. Finished tasks should influence you only through imported rollups.\n\n"
            f"{flow.flow_prompt_block()}\n\n"
            "## Active task note\n"
            f"{flow.active_task_prompt_block()}\n"
        )

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
        selected_policies: list[str],
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
            "selected_policies": selected_policies,
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
    ) -> str:
        try:
            result = self.tool_registry.run(name, arguments)
        except Exception as exc:  # pragma: no cover - defensive runtime catch
            payload = {
                "success": False,
                "summary": f"Tool crashed: {exc}",
                "data": {},
                "metadata": {"error": "tool_exception"},
            }
            return safe_json(payload)
        # Providers emit the canonical tool_result event after execute_tool returns.
        return result.as_text(limit=self.tool_registry.context.config.tools.max_tool_output_chars)

    def _run_selected_tool(
        self,
        route: RouteDecision,
        allowed_names: set[str],
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        if allowed_names and name not in allowed_names:
            payload = {
                "success": False,
                "summary": (
                    f"Tool `{name}` is not exposed for route `{route.task_signature}`. "
                    "Use one of the selected tools for this task instead."
                ),
                "data": {
                    "requested_tool": name,
                    "allowed_tools": sorted(allowed_names),
                    "route": route.task_signature,
                },
                "metadata": {"error": "tool_not_exposed"},
            }
            return safe_json(payload)
        return self._run_tool(name, arguments)

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
            trace_path = self._write_trace(trace)
            if route.lane != Lane.META:
                self.sessions.record_turn(
                    session,
                    prompt=prompt,
                    answer=text,
                    task_signature=route.task_signature,
                    verification=verification,
                    trace=trace,
                    usage=usage,
                    execution_cwd=self.tool_registry.context.execution_relative,
                    trace_path=trace_path,
                )
            else:
                self.sessions.save(session)
            try:
                self.learning_manager.record_query(
                    task_signature=route.task_signature,
                    skills_used=trace.get("selected_skills") or [],
                    policies_used=trace.get("selected_policies") or [],
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
            payload = tool_event_payload(event, exact=True)
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
                payload = tool_event_payload(event, exact=True)
                data = payload.get("data")
                metadata = payload.get("metadata")
                if isinstance(data, dict):
                    path = str(data.get("path", "")).strip()
                if not path and isinstance(metadata, dict):
                    path = str(metadata.get("path", "")).strip()
            if not path:
                for artifact in tool_event_artifacts(event):
                    if artifact.get("kind") == "path":
                        path = str(artifact.get("ref") or "").strip()
                        if path:
                            break
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
        if (
            route.source == "project_context"
            and route.task_signature == "repo/shell_execution"
            and self._looks_like_atomic_workspace_task(prompt)
        ):
            candidate = extract_json_candidate(text)
            if candidate:
                return candidate
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
                    return safe_json(payload)
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
        should_repair = route.task_signature == "extract/general"
        if (
            not should_repair
            and route.task_signature == "repo/shell_execution"
            and self._looks_like_atomic_workspace_task(prompt)
        ):
            stripped = text.strip()
            should_repair = stripped.startswith("```json") or stripped.startswith("[") or stripped.startswith("{")
        if not should_repair or stream:
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
            payload = tool_event_brief_for_prompt(event)
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
        if route.task_signature in {"research/live_compare/general", "site/understanding/general"}:
            return 10
        if route.lane == Lane.DEEP:
            return 10
        if route.task_signature in {"extract/general", "data/spreadsheet/analysis"}:
            return 8
        return 8

    @staticmethod
    def _truncate_text(value: Any, limit: int = 1200) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    # Match a real shell-command invocation literal. Rejects code-fence
    # language tags like ```python\ndef... (the interpreter is followed only
    # by a newline + non-file word, which doesn't match any of the argument
    # alternatives). Accepts:
    #   - python3 divider.py        (interpreter + file.ext)
    #   - python3 -c "..."          (interpreter + -flag + arg)
    #   - bash script.sh            (interpreter + file.ext)
    #   - npx playwright test       (runner + command)
    #   - uv run pytest             (runner + command)
    _RETRO_SHELL_CMD_RE = re.compile(
        r"(?:^|[\s`>$])(?:"
        r"(?:python3?|node|ruby|bash|sh|zsh|deno|bun)\s+"
        r"(?:-[a-zA-Z]\b\s+\S+|['\"][^'\"\n]+?['\"]|[^\s`\n]+\.\w+)"
        r"|"
        r"(?:npx|uv\s+run|pnpm|yarn|npm)\s+[^\s`\n]+"
        r")",
        re.IGNORECASE | re.MULTILINE,
    )

    def _retrospective_style_gaps(self, output: str, context) -> list[dict[str, str]]:
        """Identify retrospective style requirements not satisfied by `output`.

        Each retrospective in `context.student_notes` can advertise style
        families (shell / format / tool-use). For the `shell` family the
        expectation is that the answer text includes a shell-command
        invocation literal (interpreter + arg) — not a paraphrase, not just
        an 'Execution Output:' block, and not an `if __name__ == "__main__":`
        self-test without any shell invocation. This returns the list of gaps
        (empty if all requirements satisfied, or if no retrospective style
        requirements are present at all).

        Only `shell` carries a hard textual pattern requirement today; other
        families produce no gap records (they're honored via system prompt
        guidance only).
        """
        from rocky.core.system_prompt import _detect_style_families

        notes = getattr(context, "student_notes", None) or []
        retro_notes = [n for n in notes if str(n.get("kind") or "") == "retrospective"]
        if not retro_notes:
            return []
        # Collect the distinct style families demanded across retrospectives.
        families: list[str] = []
        for note in retro_notes:
            for family in _detect_style_families(note):
                if family not in families:
                    families.append(family)
        gaps: list[dict[str, str]] = []
        if "shell" in families:
            if not self._RETRO_SHELL_CMD_RE.search(output or ""):
                retro_titles = ", ".join(
                    str(n.get("title") or "").strip()[:80]
                    for n in retro_notes[:2]
                    if n.get("title")
                )[:200]
                gaps.append(
                    {
                        "family": "shell",
                        "rationale": (
                            "A prior retrospective describing shell-based "
                            "verification applies to this task "
                            f"({retro_titles}) but the candidate answer "
                            "does not include an explicit shell-command "
                            "invocation literal (e.g. `python3 file.py`, "
                            "`python3 -c \"...\"`, `bash script.sh`). The "
                            "retrospective's repeat-next-time workflow said "
                            "to execute the created artifact via the shell; "
                            "showing only an in-file `__main__` block or a "
                            "stand-alone 'Execution Output' block without "
                            "the invoking command does not satisfy that "
                            "workflow."
                        ),
                    }
                )
        return gaps

    def _repair_retrospective_style_gap(
        self,
        provider,
        *,
        prompt: str,
        output: str,
        route: RouteDecision,
        context,
        tool_events: list[dict[str, Any]],
        gaps: list[dict[str, str]],
        stream: bool,
    ) -> str:
        """Re-invoke the provider with an explicit repair instruction.

        Keeps the observed tool evidence in the repair prompt so the model
        can quote the actual command it ran (from run_shell_command events)
        rather than inventing one. If the tool events include a matching
        shell invocation, ask the model to surface that literal; otherwise
        ask it to add the minimal invocation its retrospective calls for.
        Non-streaming only — repair is a synchronous re-generation.
        """
        if stream or not gaps:
            return output
        gap_text = "\n".join(f"- {g['rationale']}" for g in gaps)
        # Quote ACTUAL shell commands from this turn's tool events so the
        # repaired answer is grounded in what happened, not fabrication.
        observed_commands: list[str] = []
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if str(event.get("name") or "") != "run_shell_command":
                continue
            args = event.get("arguments") or {}
            cmd = str(args.get("command") or "").strip()
            if cmd and cmd not in observed_commands:
                observed_commands.append(cmd)
        observed_block = (
            "Observed shell commands from this turn's tool evidence:\n"
            + "\n".join(f"  - {c[:240]}" for c in observed_commands[:4])
            if observed_commands
            else "No shell commands were actually executed in this turn."
        )
        repair_prompt = (
            f"Task signature: {route.task_signature}\n\n"
            f"Original user request:\n{self._truncate_text(prompt, 1600)}\n\n"
            f"Your candidate answer:\n{self._truncate_text(output, 3200)}\n\n"
            f"{observed_block}\n\n"
            "Retrospective style-gap findings:\n"
            f"{gap_text}\n\n"
            "Rewrite your candidate answer so the retrospective's workflow is visible. "
            "Keep the substantive content, but add (or replace) a fenced shell/bash code "
            "block that shows the exact interpreter invocation used to verify the "
            "artifact (e.g. `python3 divider.py` or `python3 -c \"...\"`). If you ran "
            "such a command this turn, quote the exact command from the observed block "
            "above. If you did not run one, add a single line that WOULD verify this "
            "task via the shell (do not fabricate output; name the command only).\n"
            "Return the rewritten answer text only — no JSON wrapping, no meta-commentary."
        )
        try:
            response = provider.complete(
                system_prompt=(
                    "You rewrite candidate agent answers so they visibly follow "
                    "prior-session retrospective workflows. You preserve facts, "
                    "add only the minimum shell-command literal the retrospective "
                    "style demands, and never fabricate tool output."
                ),
                messages=[Message(role="user", content=repair_prompt)],
                stream=False,
                event_handler=None,
            )
        except Exception:
            return output
        repaired = (response.text or "").strip()
        if not repaired:
            return output
        # Only accept the repair if it closes the gap. If still missing a
        # shell-command literal, keep the original answer — honest failure
        # beats a cosmetic paraphrase.
        if self._retrospective_style_gaps(repaired, context):
            return output
        return repaired

    def _learned_constraint_records(self, context) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in context.learned_policies:
            promotion_state = str(item.get("promotion_state") or "promoted").lower()
            if promotion_state != "promoted":
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            required_behavior = [
                str(rule).strip()
                for rule in (item.get("required_behavior") or [])
                if str(rule).strip()
            ]
            prohibited_behavior = [
                str(rule).strip()
                for rule in (item.get("prohibited_behavior") or [])
                if str(rule).strip()
            ]
            evidence_requirements = [
                str(rule).strip()
                for rule in (item.get("evidence_requirements") or [])
                if str(rule).strip()
            ]
            teacher_feedback = str(item.get("feedback_excerpt") or "").strip()
            if not teacher_feedback:
                text = str(item.get("text") or "")
                match = re.search(r"## Correction from the user\s+(.*?)(?:\n## |\Z)", text, flags=re.S)
                if match:
                    teacher_feedback = self._truncate_text(match.group(1).strip(), 800)
            if not required_behavior and not prohibited_behavior and not evidence_requirements and not teacher_feedback:
                continue
            records.append(
                {
                    "name": name,
                    "required_behavior": required_behavior[:4],
                    "prohibited_behavior": prohibited_behavior[:4],
                    "evidence_requirements": evidence_requirements[:4],
                    "teacher_feedback": teacher_feedback,
                }
            )
        return records[:4]

    def _learned_constraint_evidence(self, tool_events: list[dict[str, Any]]) -> str:
        relevant: list[str] = []
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            name = str(event.get("name") or "tool_result").strip() or "tool_result"
            arguments = safe_json(event.get("arguments") or {})
            text = self._truncate_text(tool_event_brief_for_prompt(event), 900)
            relevant.append(f"{name} args={arguments}\n{text}")
        return "\n\n".join(relevant[-4:]) or "No successful tool evidence was captured."

    def _teacher_exclusion_terms(self, records: list[dict[str, Any]], prompt: str) -> list[str]:
        prompt_lower = prompt.lower()
        terms: list[str] = []
        seen: set[str] = set()
        for item in records:
            feedback = str(item.get("teacher_feedback") or "").strip()
            if not feedback:
                continue
            lowered = feedback.lower()
            prior_term = ""
            treat_match = re.search(
                r"treat\s+[`'\"]?(?P<term>[^`'\"\n]+?)['`\"]?\s+as\s+(?:a|an)\s+distinct",
                feedback,
                flags=re.I,
            )
            if treat_match:
                prior_term = treat_match.group("term").strip()
            if "exclude it" in lowered and prior_term:
                candidate = prior_term
                normalized = candidate.lower()
                if normalized and normalized not in prompt_lower and normalized not in seen:
                    seen.add(normalized)
                    terms.append(normalized)
            for match in re.finditer(
                r"(?:exclude|omit|remove|do not include)\s+[`'\"]?(?P<term>[^`'\"\n,.]+?)['`\"]?(?:[\s.,;]|$)",
                feedback,
                flags=re.I,
            ):
                candidate = match.group("term").strip()
                normalized = candidate.lower()
                if normalized and normalized not in prompt_lower and normalized not in seen:
                    seen.add(normalized)
                    terms.append(normalized)
        return terms[:6]

    def _filter_output_by_teacher_terms(self, output: str, *, prompt: str, context) -> str:
        records = self._learned_constraint_records(context)
        terms = self._teacher_exclusion_terms(records, prompt)
        if not terms:
            return output
        candidate = extract_json_candidate(output)
        if not candidate:
            return output
        try:
            payload = json.loads(candidate)
        except Exception:
            return output
        if not isinstance(payload, list):
            return output
        filtered: list[Any] = []
        changed = False
        for item in payload:
            if not isinstance(item, dict):
                filtered.append(item)
                continue
            haystack = " ".join(
                str(item.get(field) or "")
                for field in ("product_name", "reason", "name", "title")
            ).lower()
            if any(term in haystack for term in terms):
                changed = True
                continue
            filtered.append(item)
        if not changed:
            return output
        return safe_json(filtered)

    def _judge_learned_constraints(
        self,
        provider,
        *,
        prompt: str,
        output: str,
        route: RouteDecision,
        context,
        tool_events: list[dict[str, Any]],
    ) -> VerificationResult:
        records = self._learned_constraint_records(context)
        if not records:
            return VerificationResult("learned_constraints_judge_v1", "pass", "")
        constraints = []
        for item in records:
            parts = [f"Skill: {item['name']}"]
            if item.get("teacher_feedback"):
                parts.append(
                    "Original teacher correction:\n"
                    + self._truncate_text(item["teacher_feedback"], 800)
                )
            if item["prohibited_behavior"]:
                parts.append("Do not:\n" + "\n".join(f"- {rule}" for rule in item["prohibited_behavior"]))
            if item["required_behavior"]:
                parts.append("Do:\n" + "\n".join(f"- {rule}" for rule in item["required_behavior"]))
            if item["evidence_requirements"]:
                parts.append(
                    "Evidence requirements:\n"
                    + "\n".join(f"- {rule}" for rule in item["evidence_requirements"])
                )
            constraints.append("\n".join(parts))
        constraints_text = "\n\n".join(constraints)
        judge_prompt = (
            f"Task signature: {route.task_signature}\n\n"
            f"Original task:\n{self._truncate_text(prompt, 2400)}\n\n"
            "Retrieved learned constraints:\n"
            f"{self._truncate_text(constraints_text, 4000)}\n\n"
            "Observed tool evidence:\n"
            f"{self._learned_constraint_evidence(tool_events)}\n\n"
            "Candidate final answer:\n"
            f"{self._truncate_text(output, 4000)}\n\n"
            "Decide whether the candidate final answer violates any retrieved learned constraint.\n"
            "Treat explicit inclusions and exclusions from the original teacher correction and the learned constraints as hard rules for the final deliverable unless the observed evidence contradicts them.\n"
            "Mark fail if the final deliverable still includes something that a learned constraint says to exclude, "
            "even when the answer labels it as uncertain, distinct, or probably wrong.\n"
            "Mark fail if the final deliverable drops or empties out a candidate family that the teacher correction explicitly said to keep.\n"
            "If the answer omits prohibited items from the deliverable and only mentions them as background evidence, pass.\n"
            "Use only the provided task, answer, learned constraints, and tool evidence.\n"
            'Return JSON only in the form {"status":"pass"|"fail","reason":"short reason","violated_rules":[str]}.'
        )
        try:
            response = provider.complete(
                system_prompt=(
                    "You verify whether Rocky's candidate final answer obeys retrieved learned constraints. "
                    "Return JSON only."
                ),
                messages=[Message(role="user", content=judge_prompt)],
                stream=False,
                event_handler=None,
            )
        except Exception:
            return VerificationResult("learned_constraints_judge_v1", "pass", "")
        candidate = extract_json_candidate(response.text)
        if not candidate:
            return VerificationResult("learned_constraints_judge_v1", "pass", "")
        try:
            payload = json.loads(candidate)
        except Exception:
            return VerificationResult("learned_constraints_judge_v1", "pass", "")
        if not isinstance(payload, dict):
            return VerificationResult("learned_constraints_judge_v1", "pass", "")
        status = str(payload.get("status", "pass")).strip().lower()
        reason = str(payload.get("reason", "")).strip()
        violated_rules = [
            str(rule).strip()
            for rule in (payload.get("violated_rules") or [])
            if str(rule).strip()
        ][:6]
        if status != "fail":
            return VerificationResult("learned_constraints_judge_v1", "pass", reason)
        return VerificationResult(
            "learned_constraints_judge_v1",
            "fail",
            reason
            or "Learned constraint violation: the candidate final answer still includes something that should be excluded.",
            failure_class="learned_constraint_violation",
            memory_promotion_allowed=False,
            learning_promotion_allowed=False,
            details={"violated_rules": violated_rules},
        )

    def _repair_learned_constraint_output(
        self,
        provider,
        *,
        prompt: str,
        output: str,
        route: RouteDecision,
        context,
        tool_events: list[dict[str, Any]],
        verification_message: str,
        stream: bool,
    ) -> str:
        if stream:
            return output
        records = self._learned_constraint_records(context)
        if not records:
            return output
        constraints = []
        for item in records:
            parts = [f"Skill: {item['name']}"]
            if item.get("teacher_feedback"):
                parts.append(
                    "Original teacher correction:\n"
                    + self._truncate_text(item["teacher_feedback"], 800)
                )
            if item["prohibited_behavior"]:
                parts.append("Do not:\n" + "\n".join(f"- {rule}" for rule in item["prohibited_behavior"]))
            if item["required_behavior"]:
                parts.append("Do:\n" + "\n".join(f"- {rule}" for rule in item["required_behavior"]))
            if item["evidence_requirements"]:
                parts.append(
                    "Evidence requirements:\n"
                    + "\n".join(f"- {rule}" for rule in item["evidence_requirements"])
                )
            constraints.append("\n".join(parts))
        constraints_text = "\n\n".join(constraints)
        wants_json = bool(extract_json_candidate(output) or output.strip().startswith(("[", "{")))
        repair_prompt = (
            f"Task signature: {route.task_signature}\n\n"
            f"Original task:\n{self._truncate_text(prompt, 2400)}\n\n"
            "Observed tool evidence:\n"
            f"{self._learned_constraint_evidence(tool_events)}\n\n"
            "Teacher corrections and learned constraints:\n"
            f"{self._truncate_text(constraints_text, 4000)}\n\n"
            "Draft that failed learned-constraint verification:\n"
            f"{self._truncate_text(output, 4000)}\n\n"
            f"Failure reason:\n{self._truncate_text(verification_message, 1200)}\n\n"
            "Rewrite the final answer so it satisfies the teacher correction and the learned constraints while staying within the observed evidence.\n"
            "Keep anything the teacher explicitly said to keep. Remove only the violating parts.\n"
            "Do not invent new candidates, files, commands, or facts.\n"
            "Prefer exact observed tokens in the reasons instead of qualitative interpretations.\n"
        )
        if wants_json:
            repair_prompt += "Return valid JSON only with no prose or markdown."
        else:
            repair_prompt += "Return only the corrected final answer with no extra commentary."
        try:
            repair_response = provider.complete(
                system_prompt=(
                    "You repair Rocky's final answer so it obeys the teacher correction, learned constraints, and observed evidence."
                ),
                messages=[Message(role="user", content=repair_prompt)],
                stream=False,
                event_handler=None,
            )
        except Exception:
            return output
        if wants_json:
            candidate = extract_json_candidate(repair_response.text)
            return candidate or output
        repaired = str(repair_response.text or "").strip()
        return repaired or output

    def _verification_repair_prompt(
        self,
        prompt: str,
        route: RouteDecision,
        verification_message: str,
    ) -> str:
        route_hint = "Use more tools if needed before answering."
        extra_hint = ""
        if route.task_signature == "repo/shell_execution":
            route_hint = (
                "Do the execution first, then use separate follow-up inspection steps "
                "to verify or summarize the result instead of bundling everything into one tool call."
            )
            if any(phrase in prompt.lower() for phrase in ("explore the response", "analyze the response", "inspect the response", "candidates to merge", "candidate", "merge")):
                route_hint = (
                    "Execute the referenced workspace command or script first. Then use a separate follow-up parsing "
                    "step, usually another focused shell command or `read_file`, to inspect the observed response before making decisions. If the "
                    "user asked for a result file, create it from the observed data and reread or verify it before "
                    "answering. If the user did not ask for a file, keep the decision output in the final answer "
                    "instead of creating new files. If the script is not executable or returns permission denied, "
                    "rerun it through an interpreter such as `sh x.sh` or `python3 tool.py`. If the live response "
                    "returns an auth, permission, network, or other error payload, report that you cannot make the "
                    "requested decision from live evidence instead of using previous traces or memories as a "
                    "substitute. Do not stop after only printing or paraphrasing the raw command output."
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
                "Use more than one spreadsheet-analysis step. Inspect the named CSV or workbook with shell first, "
                "then follow up with another shell command or `read_file` before answering."
            )
        elif route.task_signature == "extract/general":
            route_hint = (
                "Use at least two extraction steps: inspect or discover the source, then parse or classify it with shell or `read_file`, "
                "and return the final JSON only."
            )
        elif route.task_signature == "local/runtime_inspection":
            route_hint = (
                "Start with `run_shell_command` and inspect the exact local runtime paths or versions directly before answering."
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
        elif route.task_signature in {"research/live_compare/general", "site/understanding/general"}:
            minimum_items = requested_minimum_list_items(prompt)
            route_hint = (
                "Use live research tools to gather stronger evidence before answering. Search first when needed, "
                "then open or inspect a live source page. If `agent_browser` fails or is unavailable, do not try "
                "to install browsers or packages from the shell; fall back to `fetch_url`."
            )
            if prompt_requests_list_output(prompt):
                if minimum_items > 0:
                    route_hint += (
                        f" The user asked for at least {minimum_items} items. Do not stop at search-result titles "
                        "alone. Open a listing page, inspect observed candidate items, and only include items whose names or "
                        "URLs were observed in live tool output. If one page does not yield enough items, continue "
                        "with different filter or search pages on the same live source and aggregate unique observed "
                        "items across pages. Do not reopen the same page repeatedly. If you still cannot verify "
                        "enough items, say that clearly instead of padding the list with guesses."
                    )
                else:
                    route_hint += (
                        " For list-style live research, do not stop at search-result titles alone. Open a listing "
                        "page, inspect candidate items, and only include items whose names or URLs were observed in "
                        "live tool output."
                    )
        if "learned constraint" in verification_message.lower():
            extra_hint = (
                "If a learned rule excludes a candidate, claim, file, or action, remove it from the final deliverable "
                "instead of leaving it in place with a warning label.\n"
            )
        if "unsupported deterministic claims" in verification_message.lower():
            extra_hint += (
                "Rewrite the answer so every deterministic statement is grounded in exact observed strings from this "
                "run's tool output or the user's prompt. Prefer explicit token comparisons over qualitative "
                "interpretations that were not directly observed.\n"
            )
        return (
            f"Original task:\n{prompt}\n\n"
            f"Your previous attempt did not pass verification:\n{verification_message}\n\n"
            f"{route_hint}\n"
            f"{extra_hint}"
            "Continue the task now, use more tools if needed, and return the corrected final answer."
        )

    def _verification_repair_evidence(
        self,
        route: RouteDecision,
        tool_events: list[dict[str, Any]],
    ) -> str:
        successful_results = [
            ensure_tool_result_event(event)
            for event in tool_events
            if event.get("type") == "tool_result" and event.get("success", True)
        ]
        if not successful_results:
            return ""
        if route.task_signature in {"research/live_compare/general", "site/understanding/general"}:
            prioritized = [
                event
                for event in successful_results
                if event.get("name") in {"agent_browser", "fetch_url", "search_web"}
            ]
            selected = prioritized[-4:] or successful_results[-3:]
        else:
            selected = successful_results[-3:]
        blocks: list[str] = []
        remaining = 2500
        for event in selected:
            name = str(event.get("name") or "tool_result")
            brief = tool_event_brief_for_prompt(event, limit=min(900, remaining))
            if not brief:
                continue
            block = f"Tool `{name}` evidence:\n{brief}"
            if len(block) > remaining:
                block = block[:remaining].rstrip()
            if not block:
                continue
            blocks.append(block)
            remaining -= len(block) + 2
            if remaining <= 0:
                break
        return "\n\n".join(blocks).strip()

    def _successful_live_tool_targets(self, tool_events: list[dict[str, Any]]) -> set[tuple[str, str]]:
        targets: set[tuple[str, str]] = set()
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            name = str(event.get("name") or "")
            if name not in {"fetch_url", "agent_browser"}:
                continue
            arguments = event.get("arguments") or {}
            payload = tool_event_payload(event, exact=True)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            url = self._live_tool_requested_url(name, arguments) or str(data.get("url") or data.get("final_url") or "").strip()
            if url:
                targets.add((name, url))
        return targets

    def _explicit_live_url_in_prompt(self, prompt: str) -> str:
        match = re.search(r"https?://\S+", prompt)
        if not match:
            return ""
        return match.group(0).rstrip(").,;:!?]")

    def _has_attempted_fetch_url(
        self,
        prompt_url: str,
        tool_events: list[dict[str, Any]] | None,
    ) -> bool:
        if not prompt_url or not tool_events:
            return False
        for event in tool_events:
            if event.get("type") != "tool_result":
                continue
            if str(event.get("name") or "") != "fetch_url":
                continue
            arguments = event.get("arguments") or {}
            attempted = str(arguments.get("url") or "").strip().rstrip(").,;:!?]")
            if attempted == prompt_url:
                return True
        return False

    def _attempted_fetch_url_targets(self, tool_events: list[dict[str, Any]] | None) -> set[str]:
        targets: set[str] = set()
        if not tool_events:
            return targets
        for event in tool_events:
            if event.get("type") != "tool_result":
                continue
            if str(event.get("name") or "") != "fetch_url":
                continue
            arguments = event.get("arguments") or {}
            attempted = str(arguments.get("url") or "").strip().rstrip(").,;:!?]")
            if attempted:
                targets.add(attempted)
        return targets

    def _payload_error_code(self, payload: dict[str, Any]) -> str:
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            return str(metadata.get("error") or "").strip()
        return ""

    def _browser_runtime_unavailable_seen(self, tool_events: list[dict[str, Any]] | None) -> bool:
        if not tool_events:
            return False
        for event in tool_events:
            if event.get("type") != "tool_result" or event.get("success", True):
                continue
            if str(event.get("name") or "") != "agent_browser":
                continue
            payload = tool_event_payload(event, exact=True)
            if self._payload_error_code(payload) == "browser_runtime_unavailable":
                return True
        return False

    def _research_follow_up_suggestions(self, prompt: str, tool_events: list[dict[str, Any]]) -> str:
        if not prompt_requests_list_output(prompt):
            return ""
        tokens: dict[str, int] = {}
        for event in tool_events:
            if event.get("type") != "tool_result" or not event.get("success", True):
                continue
            if str(event.get("name") or "") not in {"fetch_url", "agent_browser"}:
                continue
            payload = tool_event_payload(event, exact=True)
            data = payload.get("data")
            items: list[dict[str, Any]] = []
            if isinstance(data, dict):
                if str(event.get("name") or "") == "agent_browser":
                    items = [item for item in list(data.get("items") or []) if isinstance(item, dict)]
                else:
                    items = [item for item in list(data.get("link_items") or []) if isinstance(item, dict)]
            elif isinstance(data, list):
                items = [item for item in data if isinstance(item, dict)]
            for item in items[:20]:
                fragments: list[str] = []
                url = str(item.get("url") or "").strip()
                if url:
                    parsed = urlparse(url)
                    fragments.extend(part for part in parsed.path.split("/") if part)
                for field in ("name", "text", "title"):
                    value = str(item.get(field) or "").strip()
                    if value:
                        if "/" in value:
                            fragments.append(value.rsplit("/", 1)[-1])
                        else:
                            fragments.append(value)
                for fragment in fragments:
                    for raw_token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{2,}", fragment):
                        normalized_full = raw_token.lower().strip(".-+")
                        variants = {normalized_full}
                        base_variant = re.split(r"[-+]", normalized_full, maxsplit=1)[0].strip(".-+")
                        if base_variant:
                            variants.add(base_variant)
                        for normalized in variants:
                            if normalized in self._RESEARCH_TOKEN_STOPWORDS:
                                continue
                            if len(normalized) < 4:
                                continue
                            tokens[normalized] = tokens.get(normalized, 0) + 1
        ranked = [token for token, _count in sorted(tokens.items(), key=lambda item: (-item[1], item[0]))[:5]]
        if not ranked:
            return ""
        return (
            "Observed candidate family tokens from prior live pages: "
            + ", ".join(ranked)
            + ". If you still need more verified items, try one or two new same-site filter/search URLs using those tokens instead of repeating the same page or the same failed external search."
        )

    def _live_tool_requested_url(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "fetch_url":
            return str(arguments.get("url") or "").strip()
        if name == "agent_browser":
            command = str(arguments.get("command") or "").strip()
            if command.startswith("open "):
                parts = command.split(maxsplit=1)
                if len(parts) == 2 and parts[1].startswith(("http://", "https://")):
                    return parts[1].strip()
        return ""

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
        return None

    def _browser_dependency_install_guard(
        self,
        route: RouteDecision,
        prompt: str,
        name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        if route.task_signature not in {"research/live_compare/general", "site/understanding/general"}:
            return None
        if name != "run_shell_command":
            return None
        lowered_prompt = prompt.lower()
        if any(
            phrase in lowered_prompt
            for phrase in (
                "install playwright",
                "playwright install",
                "install browser",
                "set up playwright",
                "setup playwright",
            )
        ):
            return None
        lowered_command = str(arguments.get("command") or "").lower()
        install_markers = (
            "playwright install",
            "python -m playwright install",
            "python3 -m playwright install",
            "pip install playwright",
            "python -m pip install playwright",
            "python3 -m pip install playwright",
            "npm install playwright",
            "npx playwright install",
            "brew install playwright",
        )
        if not any(marker in lowered_command for marker in install_markers):
            return None
        payload = {
            "success": False,
            "summary": (
                "Use the dedicated `agent_browser` tool for browser work. Do not install browsers or Playwright from "
                "shell during live research. Fall back to `fetch_url` when browser automation is unavailable."
            ),
            "data": {},
            "metadata": {"error": "use_web_fallback_after_browser_failure"},
        }
        return safe_json(payload)

    def _research_explicit_url_guard(
        self,
        route: RouteDecision,
        prompt: str,
        name: str,
        arguments: dict[str, Any],
        pending_explicit_fetch_url: bool,
    ) -> str | None:
        if route.task_signature != "research/live_compare/general" or not pending_explicit_fetch_url:
            return None
        prompt_url = self._explicit_live_url_in_prompt(prompt)
        if not prompt_url:
            return None
        requested_url = str(arguments.get("url") or "").strip().rstrip(").,;:!?]")
        if name == "fetch_url" and requested_url == prompt_url:
            return None
        payload = {
            "success": False,
            "summary": (
                f"The prompt already gives {prompt_url}. Start with `fetch_url` on that exact URL before using "
                f"`{name}` or switching to other pages."
            ),
            "data": {"url": prompt_url, "requested_tool": name},
            "metadata": {"error": "use_explicit_url_first"},
        }
        return safe_json(payload)

    def _browser_runtime_unavailable_guard(
        self,
        route: RouteDecision,
        name: str,
        browser_runtime_unavailable: bool,
    ) -> str | None:
        if route.task_signature not in {"research/live_compare/general", "site/understanding/general"}:
            return None
        if name != "agent_browser" or not browser_runtime_unavailable:
            return None
        payload = {
            "success": False,
            "summary": (
                "`agent_browser` is already known to be unavailable in this turn. Do not retry it. "
                "Continue with `fetch_url` or `search_web` instead."
            ),
            "data": {},
            "metadata": {"error": "use_web_fallback_after_browser_failure"},
        }
        return safe_json(payload)

    def _research_fetch_before_browser_guard(
        self,
        route: RouteDecision,
        name: str,
        arguments: dict[str, Any],
        attempted_fetch_urls: set[str],
    ) -> str | None:
        if route.task_signature != "research/live_compare/general" or name != "agent_browser":
            return None
        url = self._live_tool_requested_url(name, arguments).rstrip(").,;:!?]")
        if not url or url in attempted_fetch_urls:
            return None
        payload = {
            "success": False,
            "summary": (
                f"For live research, use `fetch_url` on {url} before opening it with `agent_browser`. "
                "Escalate to browser only if the fetched page still leaves missing evidence."
            ),
            "data": {"url": url, "requested_tool": name},
            "metadata": {"error": "use_fetch_url_before_browser"},
        }
        return safe_json(payload)

    def _duplicate_live_page_guard(
        self,
        route: RouteDecision,
        name: str,
        arguments: dict[str, Any],
        successful_live_targets: set[tuple[str, str]],
    ) -> str | None:
        if route.task_signature not in {"research/live_compare/general", "site/understanding/general"}:
            return None
        if name not in {"fetch_url", "agent_browser"}:
            return None
        url = self._live_tool_requested_url(name, arguments)
        if not url or (name, url) not in successful_live_targets:
            return None
        payload = {
            "success": False,
            "summary": (
                f"`{name}` already succeeded for {url}. Reuse the evidence from that page. "
                "If you still need more items, choose a different live page or a different filter/search URL."
            ),
            "data": {"url": url, "tool": name},
            "metadata": {"error": "reuse_previous_live_page_evidence"},
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
            f"{event.get('name', 'tool_result')} {index}:\n{tool_event_brief_for_prompt(event, exact=True)}"
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
        prior_tool_events: list[dict[str, Any]],
        *,
        stream: bool,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ):
        successful_tool_names: list[str] = []
        successful_live_targets = self._successful_live_tool_targets(prior_tool_events)
        prompt_url = self._explicit_live_url_in_prompt(prompt)
        pending_explicit_fetch_url = bool(prompt_url and not self._has_attempted_fetch_url(prompt_url, prior_tool_events))
        browser_runtime_unavailable = self._browser_runtime_unavailable_seen(prior_tool_events)
        attempted_fetch_urls = self._attempted_fetch_url_targets(prior_tool_events)

        def execute_tool(name: str, arguments: dict[str, Any]) -> str:
            nonlocal pending_explicit_fetch_url, browser_runtime_unavailable
            guarded = self._research_explicit_url_guard(
                route,
                prompt,
                name,
                arguments,
                pending_explicit_fetch_url,
            )
            if guarded is not None:
                return guarded
            guarded = self._research_fetch_before_browser_guard(
                route,
                name,
                arguments,
                attempted_fetch_urls,
            )
            if guarded is not None:
                return guarded
            guarded = self._automation_shell_write_guard(
                route,
                prompt,
                name,
                arguments,
                successful_tool_names,
            )
            if guarded is not None:
                return guarded
            guarded = self._shell_follow_up_guard(
                route,
                prompt,
                name,
                successful_tool_names,
            )
            if guarded is not None:
                return guarded
            guarded = self._browser_runtime_unavailable_guard(
                route,
                name,
                browser_runtime_unavailable,
            )
            if guarded is not None:
                return guarded
            guarded = self._duplicate_live_page_guard(
                route,
                name,
                arguments,
                successful_live_targets,
            )
            if guarded is not None:
                return guarded
            text = self._run_selected_tool(
                route,
                selected_tool_names,
                name,
                arguments,
            )
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            if name == "fetch_url":
                requested_url = str(arguments.get("url") or "").strip().rstrip(").,;:!?]")
                if requested_url:
                    attempted_fetch_urls.add(requested_url)
            if payload.get("success", False):
                successful_tool_names.append(name)
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                if name == "fetch_url" and pending_explicit_fetch_url:
                    if prompt_url and requested_url == prompt_url:
                        pending_explicit_fetch_url = False
                url = self._live_tool_requested_url(name, arguments) or str(data.get("url") or data.get("final_url") or "").strip()
                if name in {"fetch_url", "agent_browser"} and url:
                    successful_live_targets.add((name, url))
            elif name == "fetch_url" and pending_explicit_fetch_url:
                if prompt_url and requested_url == prompt_url:
                    pending_explicit_fetch_url = False
            if name == "agent_browser" and self._payload_error_code(payload) == "browser_runtime_unavailable":
                browser_runtime_unavailable = True
            return text

        repair_prompt = self._verification_repair_prompt(
            messages[-1].content if messages else "",
            route,
            verification_message,
        )
        prior_evidence = self._verification_repair_evidence(route, prior_tool_events)
        if prior_evidence:
            repair_prompt += (
                "\n\nPreviously gathered tool evidence from the failed attempt:\n"
                f"{prior_evidence}\n\n"
                "Reuse this evidence instead of restarting from scratch. Only make additional tool calls when you still need missing evidence."
            )
        follow_up_suggestions = self._research_follow_up_suggestions(prompt, prior_tool_events)
        if follow_up_suggestions:
            repair_prompt += "\n\n" + follow_up_suggestions
        retry_messages = [
            *messages,
            Message(role="assistant", content=normalized_text),
            Message(
                role="user",
                content=repair_prompt,
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

    def _run_flow_controlled_loop(
        self,
        *,
        prompt: str,
        route: RouteDecision,
        active_thread,
        evidence_graph,
        context,
        system_prompt: str,
        provider,
        selected_tool_objects: list[Any],
        selected_tool_names: set[str],
        stream: bool,
        event_handler: Callable[[dict[str, Any]], None] | None,
        provider_name: str,
    ) -> tuple[ProviderResponse, str, Any, VerificationResult, dict[str, Any]]:
        run_flow = RunFlowManager(
            self.tool_registry.context.workspace_root / ".rocky" / "runs",
            prompt=prompt,
            task_signature=route.task_signature,
            task_class=route.task_class.value if hasattr(route.task_class, "value") else str(route.task_class),
            execution_cwd=self.tool_registry.context.execution_relative,
            minimum_list_items=requested_minimum_list_items(prompt),
        )
        aggregate_tool_events: list[dict[str, Any]] = []
        aggregate_usage: dict[str, Any] = {}
        raw_bursts: list[dict[str, Any]] = []
        final_text = ""
        answer_contract = self.answer_contract_builder.build(
            prompt,
            route.task_signature,
            active_thread,
            evidence_graph,
            prior_answer=self.last_answer,
        )
        verification_result = VerificationResult(
            "flow_task_loop_v1",
            "fail",
            "Flow-controlled task loop ended before producing a verified final answer.",
            memory_promotion_allowed=False,
            learning_promotion_allowed=False,
        )
        max_bursts = 8 if route.task_signature.startswith(("research/", "site/")) else 4
        prompt_url = self._explicit_live_url_in_prompt(prompt)
        pending_explicit_fetch_url = bool(prompt_url)
        browser_runtime_unavailable = False
        attempted_fetch_urls: set[str] = set()
        successful_live_targets: set[tuple[str, str]] = set()

        for _burst_index in range(max_bursts):
            current_task = run_flow.run.active_task()
            burst_system_prompt = self._system_prompt_with_flow(system_prompt, run_flow)
            burst_messages = [Message(role="user", content=run_flow.user_prompt_for_burst())]

            successful_tool_names: list[str] = []

            def execute_tool(name: str, arguments: dict[str, Any]) -> str:
                nonlocal pending_explicit_fetch_url, browser_runtime_unavailable
                guarded = self._browser_dependency_install_guard(route, prompt, name, arguments)
                if guarded is not None:
                    event = ensure_tool_result_event({"type": "tool_result", "name": name, "arguments": arguments, "text": guarded})
                    run_flow.ingest_tool_event(event)
                    return guarded
                guarded = self._research_explicit_url_guard(
                    route,
                    prompt,
                    name,
                    arguments,
                    pending_explicit_fetch_url,
                )
                if guarded is not None:
                    event = ensure_tool_result_event({"type": "tool_result", "name": name, "arguments": arguments, "text": guarded})
                    run_flow.ingest_tool_event(event)
                    return guarded
                guarded = self._research_fetch_before_browser_guard(
                    route,
                    name,
                    arguments,
                    attempted_fetch_urls,
                )
                if guarded is not None:
                    event = ensure_tool_result_event({"type": "tool_result", "name": name, "arguments": arguments, "text": guarded})
                    run_flow.ingest_tool_event(event)
                    return guarded
                guarded = self._automation_shell_write_guard(
                    route,
                    prompt,
                    name,
                    arguments,
                    successful_tool_names,
                )
                if guarded is not None:
                    event = ensure_tool_result_event({"type": "tool_result", "name": name, "arguments": arguments, "text": guarded})
                    run_flow.ingest_tool_event(event)
                    return guarded
                guarded = self._shell_follow_up_guard(
                    route,
                    prompt,
                    name,
                    successful_tool_names,
                )
                if guarded is not None:
                    event = ensure_tool_result_event({"type": "tool_result", "name": name, "arguments": arguments, "text": guarded})
                    run_flow.ingest_tool_event(event)
                    return guarded
                guarded = self._browser_runtime_unavailable_guard(
                    route,
                    name,
                    browser_runtime_unavailable,
                )
                if guarded is not None:
                    event = ensure_tool_result_event({"type": "tool_result", "name": name, "arguments": arguments, "text": guarded})
                    run_flow.ingest_tool_event(event)
                    return guarded
                guarded = self._duplicate_live_page_guard(
                    route,
                    name,
                    arguments,
                    successful_live_targets,
                )
                if guarded is not None:
                    event = ensure_tool_result_event(
                        {
                            "type": "tool_result",
                            "name": name,
                            "arguments": arguments,
                            "text": guarded,
                        }
                    )
                    run_flow.ingest_tool_event(event)
                    return guarded
                text = self._run_selected_tool(route, selected_tool_names, name, arguments)
                event = ensure_tool_result_event(
                    {
                        "type": "tool_result",
                        "name": name,
                        "arguments": arguments,
                        "text": text,
                        "success": True,
                    }
                )
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = {}
                requested_url = str(arguments.get("url") or "").strip().rstrip(").,;:!?]")
                if name == "fetch_url" and requested_url:
                    attempted_fetch_urls.add(requested_url)
                if payload.get("success", False):
                    successful_tool_names.append(name)
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                    if name == "fetch_url" and pending_explicit_fetch_url and prompt_url and requested_url == prompt_url:
                        pending_explicit_fetch_url = False
                    url = self._live_tool_requested_url(name, arguments) or str(data.get("url") or data.get("final_url") or "").strip()
                    if name in {"fetch_url", "agent_browser"} and url:
                        successful_live_targets.add((name, url))
                elif name == "fetch_url" and pending_explicit_fetch_url and prompt_url and requested_url == prompt_url:
                    pending_explicit_fetch_url = False
                if name == "agent_browser" and self._payload_error_code(payload) == "browser_runtime_unavailable":
                    browser_runtime_unavailable = True
                run_flow.ingest_tool_event(event)
                return text

            prefetch_url = run_flow.suggested_fetch_url()
            if prefetch_url and "fetch_url" in selected_tool_names and prefetch_url not in attempted_fetch_urls:
                prefetch_text = execute_tool("fetch_url", {"url": prefetch_url})
                prefetch_events = self._prepare_tool_events(
                    [
                        ensure_tool_result_event(
                            {
                                "type": "tool_result",
                                "name": "fetch_url",
                                "arguments": {"url": prefetch_url},
                                "text": prefetch_text,
                            }
                        )
                    ]
                )
                aggregate_tool_events.extend(prefetch_events)
                raw_bursts.append({"task_id": current_task.task_id, "direct_tool": "fetch_url", "url": prefetch_url})
                self.evidence_accumulator.ingest_tool_events(evidence_graph, prefetch_events)
                active_thread.add_tool_events(prefetch_events)
                self._sync_thread_from_evidence(active_thread, evidence_graph)
                evidence_backed = self._try_research_evidence_backed_answer(
                    prompt=prompt,
                    route=route,
                    active_thread=active_thread,
                    evidence_graph=evidence_graph,
                    tool_events=aggregate_tool_events,
                )
                if evidence_backed is not None:
                    final_text, answer_contract, verification_result = evidence_backed
                    run_flow.advance(
                        evidence_graph=evidence_graph,
                        tool_events=aggregate_tool_events,
                        final_output_ready=True,
                    )
                    aggregate_response = ProviderResponse(
                        text=final_text,
                        usage=aggregate_usage,
                        raw={"bursts": raw_bursts, "evidence_backed_final": True},
                        tool_events=aggregate_tool_events,
                    )
                    return aggregate_response, final_text, answer_contract, verification_result, {
                        **run_flow.run_summary,
                        "provider": provider_name,
                    }
                run_flow.advance(
                    evidence_graph=evidence_graph,
                    tool_events=aggregate_tool_events,
                    final_output_ready=False,
                )
                current_task = run_flow.run.active_task()
                burst_system_prompt = self._system_prompt_with_flow(system_prompt, run_flow)
                burst_messages = [Message(role="user", content=run_flow.user_prompt_for_burst())]

            burst_response = provider.run_with_tools(
                system_prompt=burst_system_prompt,
                messages=burst_messages,
                tools=[tool.openai_schema() for tool in selected_tool_objects],
                execute_tool=execute_tool,
                max_rounds=self._tool_loop_rounds(route),
                event_handler=event_handler if stream else None,
            )
            burst_response = ProviderResponse(
                text=burst_response.text,
                usage=burst_response.usage,
                raw=burst_response.raw,
                tool_events=self._prepare_tool_events(burst_response.tool_events),
            )
            raw_bursts.append({"task_id": current_task.task_id, "raw": burst_response.raw})
            aggregate_tool_events.extend(burst_response.tool_events)
            aggregate_usage = self._merge_usage(aggregate_usage, burst_response.usage)
            self.evidence_accumulator.ingest_tool_events(evidence_graph, burst_response.tool_events)
            active_thread.add_tool_events(burst_response.tool_events)
            self._sync_thread_from_evidence(active_thread, evidence_graph)
            run_flow.note_burst_output(burst_response.text)

            if current_task.kind != "finalize":
                if burst_response.text.strip():
                    candidate_text = self._normalize_output(
                        route,
                        burst_response.text,
                        prompt,
                        aggregate_tool_events,
                    )
                    candidate_text = self._repair_structured_output(
                        provider,
                        burst_system_prompt,
                        prompt,
                        route,
                        candidate_text,
                        aggregate_tool_events,
                        stream=stream,
                    )
                    candidate_contract = self.answer_contract_builder.build(
                        prompt,
                        route.task_signature,
                        active_thread,
                        evidence_graph,
                        prior_answer=self.last_answer,
                    )
                    candidate_verification = self.verifier_registry.verify(
                        prompt=prompt,
                        route=route,
                        task_class=route.task_class,
                        output=candidate_text,
                        tool_events=aggregate_tool_events,
                        active_thread=active_thread,
                        evidence_graph=evidence_graph,
                        answer_contract=candidate_contract,
                        prior_answer=self.last_answer,
                        continuation_expected=False,
                    )
                    if candidate_verification.status == "pass" and self._should_judge_automation_output(
                        route,
                        prompt,
                        aggregate_tool_events,
                        stream=stream,
                    ):
                        candidate_verification = self._judge_automation_output(provider, prompt, aggregate_tool_events)
                    if candidate_verification.status == "pass":
                        learned_constraints_result = self._judge_learned_constraints(
                            provider,
                            prompt=prompt,
                            output=candidate_text,
                            route=route,
                            context=context,
                            tool_events=aggregate_tool_events,
                        )
                        if learned_constraints_result.status == "fail":
                            candidate_verification = learned_constraints_result
                    if candidate_verification.status == "pass":
                        run_flow.advance(
                            evidence_graph=evidence_graph,
                            tool_events=aggregate_tool_events,
                            final_output_ready=True,
                        )
                        aggregate_response = ProviderResponse(
                            text=candidate_text,
                            usage=aggregate_usage,
                            raw={"bursts": raw_bursts},
                            tool_events=aggregate_tool_events,
                        )
                        return aggregate_response, candidate_text, candidate_contract, candidate_verification, {
                            **run_flow.run_summary,
                            "provider": provider_name,
                        }
                evidence_backed = self._try_research_evidence_backed_answer(
                    prompt=prompt,
                    route=route,
                    active_thread=active_thread,
                    evidence_graph=evidence_graph,
                    tool_events=aggregate_tool_events,
                )
                if evidence_backed is not None:
                    final_text, answer_contract, verification_result = evidence_backed
                    run_flow.advance(
                        evidence_graph=evidence_graph,
                        tool_events=aggregate_tool_events,
                        final_output_ready=True,
                    )
                    aggregate_response = ProviderResponse(
                        text=final_text,
                        usage=aggregate_usage,
                        raw={"bursts": raw_bursts, "evidence_backed_final": True},
                        tool_events=aggregate_tool_events,
                    )
                    return aggregate_response, final_text, answer_contract, verification_result, {
                        **run_flow.run_summary,
                        "provider": provider_name,
                    }
                run_flow.advance(
                    evidence_graph=evidence_graph,
                    tool_events=aggregate_tool_events,
                    final_output_ready=False,
                )
                continue

            final_text = self._normalize_output(
                route,
                burst_response.text,
                prompt,
                aggregate_tool_events,
            )
            final_text = self._repair_structured_output(
                provider,
                burst_system_prompt,
                prompt,
                route,
                final_text,
                aggregate_tool_events,
                stream=stream,
            )
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
                output=final_text,
                tool_events=aggregate_tool_events,
                active_thread=active_thread,
                evidence_graph=evidence_graph,
                answer_contract=answer_contract,
                prior_answer=self.last_answer,
                continuation_expected=False,
            )
            if verification_result.status == "pass" and self._should_judge_automation_output(
                route,
                prompt,
                aggregate_tool_events,
                stream=stream,
            ):
                verification_result = self._judge_automation_output(provider, prompt, aggregate_tool_events)
            if verification_result.status == "pass":
                learned_constraints_result = self._judge_learned_constraints(
                    provider,
                    prompt=prompt,
                    output=final_text,
                    route=route,
                    context=context,
                    tool_events=aggregate_tool_events,
                )
                if learned_constraints_result.status == "fail":
                    verification_result = learned_constraints_result
            if verification_result.status == "pass":
                run_flow.advance(
                    evidence_graph=evidence_graph,
                    tool_events=aggregate_tool_events,
                    final_output_ready=True,
                )
                aggregate_response = ProviderResponse(
                    text=final_text,
                    usage=aggregate_usage,
                    raw={"bursts": raw_bursts},
                    tool_events=aggregate_tool_events,
                )
                return aggregate_response, final_text, answer_contract, verification_result, {
                    **run_flow.run_summary,
                    "provider": provider_name,
                }

            evidence_backed = self._try_research_evidence_backed_answer(
                prompt=prompt,
                route=route,
                active_thread=active_thread,
                evidence_graph=evidence_graph,
                tool_events=aggregate_tool_events,
            )
            if evidence_backed is not None:
                final_text, answer_contract, verification_result = evidence_backed
                run_flow.advance(
                    evidence_graph=evidence_graph,
                    tool_events=aggregate_tool_events,
                    final_output_ready=True,
                )
                aggregate_response = ProviderResponse(
                    text=final_text,
                    usage=aggregate_usage,
                    raw={"bursts": raw_bursts, "evidence_backed_final": True},
                    tool_events=aggregate_tool_events,
                )
                return aggregate_response, final_text, answer_contract, verification_result, {
                    **run_flow.run_summary,
                    "provider": provider_name,
                }

            run_flow.note_verification_failure(verification_result)
            if self._verification_needs_more_evidence(route, verification_result):
                continue
            if route.tool_families:
                retry_response = self._retry_after_verification_failure(
                    provider,
                    burst_system_prompt,
                    burst_messages,
                    route,
                    selected_tool_objects,
                    selected_tool_names,
                    verification_result.message,
                    final_text,
                    prompt,
                    aggregate_tool_events,
                    stream=stream,
                    event_handler=event_handler,
                )
                retry_response = ProviderResponse(
                    text=retry_response.text,
                    usage=retry_response.usage,
                    raw=retry_response.raw,
                    tool_events=self._prepare_tool_events(retry_response.tool_events),
                )
                raw_bursts.append({"task_id": current_task.task_id, "raw": retry_response.raw, "retry": True})
                aggregate_tool_events.extend(retry_response.tool_events)
                aggregate_usage = self._merge_usage(aggregate_usage, retry_response.usage)
                self.evidence_accumulator.ingest_tool_events(evidence_graph, retry_response.tool_events)
                active_thread.add_tool_events(retry_response.tool_events)
                self._sync_thread_from_evidence(active_thread, evidence_graph)
                run_flow.note_burst_output(retry_response.text)
                final_text = self._normalize_output(
                    route,
                    retry_response.text,
                    prompt,
                    aggregate_tool_events,
                )
                final_text = self._repair_structured_output(
                    provider,
                    burst_system_prompt,
                    prompt,
                    route,
                    final_text,
                    aggregate_tool_events,
                    stream=stream,
                )
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
                    output=final_text,
                    tool_events=aggregate_tool_events,
                    active_thread=active_thread,
                    evidence_graph=evidence_graph,
                    answer_contract=answer_contract,
                    prior_answer=self.last_answer,
                    continuation_expected=False,
                )
                if verification_result.status == "pass" and self._should_judge_automation_output(
                    route,
                    prompt,
                    aggregate_tool_events,
                    stream=stream,
                ):
                    verification_result = self._judge_automation_output(provider, prompt, aggregate_tool_events)
                if verification_result.status == "pass":
                    learned_constraints_result = self._judge_learned_constraints(
                        provider,
                        prompt=prompt,
                        output=final_text,
                        route=route,
                        context=context,
                        tool_events=aggregate_tool_events,
                    )
                    if learned_constraints_result.status == "fail":
                        verification_result = learned_constraints_result
                if verification_result.status == "pass":
                    run_flow.advance(
                        evidence_graph=evidence_graph,
                        tool_events=aggregate_tool_events,
                        final_output_ready=True,
                    )
                    aggregate_response = ProviderResponse(
                        text=final_text,
                        usage=aggregate_usage,
                        raw={"bursts": raw_bursts},
                        tool_events=aggregate_tool_events,
                    )
                    return aggregate_response, final_text, answer_contract, verification_result, {
                        **run_flow.run_summary,
                        "provider": provider_name,
                    }

                evidence_backed = self._try_research_evidence_backed_answer(
                    prompt=prompt,
                    route=route,
                    active_thread=active_thread,
                    evidence_graph=evidence_graph,
                    tool_events=aggregate_tool_events,
                )
                if evidence_backed is not None:
                    final_text, answer_contract, verification_result = evidence_backed
                    run_flow.advance(
                        evidence_graph=evidence_graph,
                        tool_events=aggregate_tool_events,
                        final_output_ready=True,
                    )
                    aggregate_response = ProviderResponse(
                        text=final_text,
                        usage=aggregate_usage,
                        raw={"bursts": raw_bursts, "evidence_backed_final": True},
                        tool_events=aggregate_tool_events,
                    )
                    return aggregate_response, final_text, answer_contract, verification_result, {
                        **run_flow.run_summary,
                        "provider": provider_name,
                    }

        evidence_backed = self._try_research_evidence_backed_answer(
            prompt=prompt,
            route=route,
            active_thread=active_thread,
            evidence_graph=evidence_graph,
            tool_events=aggregate_tool_events,
        )
        if evidence_backed is not None:
            final_text, answer_contract, verification_result = evidence_backed
            run_flow.advance(
                evidence_graph=evidence_graph,
                tool_events=aggregate_tool_events,
                final_output_ready=True,
            )
            aggregate_response = ProviderResponse(
                text=final_text,
                usage=aggregate_usage,
                raw={"bursts": raw_bursts, "evidence_backed_final": True},
                tool_events=aggregate_tool_events,
            )
            return aggregate_response, final_text, answer_contract, verification_result, {
                **run_flow.run_summary,
                "provider": provider_name,
            }

        aggregate_response = ProviderResponse(
            text=final_text or "Rocky did not finish the task.",
            usage=aggregate_usage,
            raw={"bursts": raw_bursts},
            tool_events=aggregate_tool_events,
        )
        return aggregate_response, aggregate_response.text, answer_contract, verification_result, {
            **run_flow.run_summary,
            "provider": provider_name,
        }

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
        route = self._maybe_upgrade_route_from_project_context(prompt, route)
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
                "selected_policies": [],
                "provider": "deterministic",
                "verification": verification,
                "tool_events": [],
                "context": {"instructions": [], "memories": [], "skills": [], "learned_policies": [], "tool_families": []},
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
                "selected_policies": [],
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
        selected_tool_objects = self.tool_registry.select_for_task(
            route.tool_families,
            route.task_signature,
            prompt,
        )
        provider = self.provider_registry.provider_for_task(needs_tools=bool(selected_tool_objects))
        selected_tools = [tool.name for tool in selected_tool_objects]
        selected_tool_names = set(selected_tools)
        selected_skills = [item["name"] for item in context.skills]
        selected_policies = [item["name"] for item in context.learned_policies]
        provider_name = provider.__class__.__name__

        if self._should_use_flow_loop(route):
            provider_response, normalized_text, answer_contract, verification_result, flow_state = self._run_flow_controlled_loop(
                prompt=prompt,
                route=route,
                active_thread=active_thread,
                evidence_graph=evidence_graph,
                context=context,
                system_prompt=system_prompt,
                provider=provider,
                selected_tool_objects=selected_tool_objects,
                selected_tool_names=selected_tool_names,
                stream=stream,
                event_handler=event_handler,
                provider_name=provider_name,
            )
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
                "selected_policies": selected_policies,
                "provider": provider_name,
                "verification": verification,
                "usage": provider_response.usage,
                "tool_events": provider_response.tool_events,
                "context": context_summary,
                "raw_provider_keys": sorted(provider_response.raw.keys()),
                "thread": thread_registry.snapshot(active_thread.thread_id),
                "answer_contract": answer_contract.as_record(),
                "supported_claims": self._supported_claim_records(evidence_graph, answer_contract),
                "run_state": flow_state,
            }
            current_thread = trace.get("thread", {}).get("current_thread") or {}
            if current_thread:
                current_thread["summary_text"] = active_thread.summary_text()
                trace["thread"]["current_thread"] = current_thread
            # Phase 2.5 O2 — retrospective style-gap repair. If a retrospective
            # tagged `shell` applies to this task but the candidate answer
            # lacks an explicit shell-command invocation literal, re-invoke
            # the provider to rewrite with the missing literal. Quotes actual
            # observed commands from tool_events so the repair is grounded.
            retro_gaps = self._retrospective_style_gaps(normalized_text, context)
            if retro_gaps and not stream:
                repaired = self._repair_retrospective_style_gap(
                    provider,
                    prompt=prompt,
                    output=normalized_text,
                    route=route,
                    context=context,
                    tool_events=provider_response.tool_events,
                    gaps=retro_gaps,
                    stream=stream,
                )
                if repaired and repaired != normalized_text:
                    normalized_text = repaired
                    trace["retrospective_repair"] = {
                        "gaps": [g["family"] for g in retro_gaps],
                        "applied": True,
                    }
                else:
                    trace["retrospective_repair"] = {
                        "gaps": [g["family"] for g in retro_gaps],
                        "applied": False,
                    }
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

        provider_response = None
        attempts = 3
        for attempt in range(attempts):
            successful_tool_names: list[str] = []
            successful_live_targets: set[tuple[str, str]] = set()
            prompt_url = self._explicit_live_url_in_prompt(prompt)
            pending_explicit_fetch_url = bool(prompt_url)
            browser_runtime_unavailable = False
            attempted_fetch_urls: set[str] = set()

            def execute_tool(name: str, arguments: dict[str, Any]) -> str:
                nonlocal pending_explicit_fetch_url, browser_runtime_unavailable
                guarded = self._browser_dependency_install_guard(
                    route,
                    prompt,
                    name,
                    arguments,
                )
                if guarded is not None:
                    return guarded
                guarded = self._research_explicit_url_guard(
                    route,
                    prompt,
                    name,
                    arguments,
                    pending_explicit_fetch_url,
                )
                if guarded is not None:
                    return guarded
                guarded = self._research_fetch_before_browser_guard(
                    route,
                    name,
                    arguments,
                    attempted_fetch_urls,
                )
                if guarded is not None:
                    return guarded
                guarded = self._automation_shell_write_guard(
                    route,
                    prompt,
                    name,
                    arguments,
                    successful_tool_names,
                )
                if guarded is not None:
                    return guarded
                guarded = self._shell_follow_up_guard(
                    route,
                    prompt,
                    name,
                    successful_tool_names,
                )
                if guarded is not None:
                    return guarded
                guarded = self._browser_runtime_unavailable_guard(
                    route,
                    name,
                    browser_runtime_unavailable,
                )
                if guarded is not None:
                    return guarded
                guarded = self._duplicate_live_page_guard(
                    route,
                    name,
                    arguments,
                    successful_live_targets,
                )
                if guarded is not None:
                    return guarded
                text = self._run_selected_tool(
                    route,
                    selected_tool_names,
                    name,
                    arguments,
                )
                try:
                    payload = json.loads(text)
                except Exception:
                    payload = {}
                if name == "fetch_url":
                    requested_url = str(arguments.get("url") or "").strip().rstrip(").,;:!?]")
                    if requested_url:
                        attempted_fetch_urls.add(requested_url)
                if payload.get("success", False):
                    successful_tool_names.append(name)
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                    if name == "fetch_url" and pending_explicit_fetch_url:
                        if prompt_url and requested_url == prompt_url:
                            pending_explicit_fetch_url = False
                    url = self._live_tool_requested_url(name, arguments) or str(data.get("url") or data.get("final_url") or "").strip()
                    if name in {"fetch_url", "agent_browser"} and url:
                        successful_live_targets.add((name, url))
                elif name == "fetch_url" and pending_explicit_fetch_url:
                    if prompt_url and requested_url == prompt_url:
                        pending_explicit_fetch_url = False
                if name == "agent_browser" and self._payload_error_code(payload) == "browser_runtime_unavailable":
                    browser_runtime_unavailable = True
                return text

            try:
                if selected_tool_objects:
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
                    "selected_policies": selected_policies,
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
                    selected_policies=selected_policies,
                    provider_name=provider_name,
                    exc=exc,
                    options=options,
                    stream=stream,
                    event_handler=event_handler,
                )
        assert provider_response is not None
        provider_response = ProviderResponse(
            text=provider_response.text,
            usage=provider_response.usage,
            raw=provider_response.raw,
            tool_events=self._prepare_tool_events(provider_response.tool_events),
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
        if verification_result.status == "pass":
            learned_constraints_result = self._judge_learned_constraints(
                provider,
                prompt=prompt,
                output=normalized_text,
                route=route,
                context=context,
                tool_events=provider_response.tool_events,
            )
            if learned_constraints_result.status == "fail":
                verification_result = learned_constraints_result
        if verification_result.status == "fail" and verification_result.name == "learned_constraints_judge_v1":
            for candidate_text in (
                self._filter_output_by_teacher_terms(
                    normalized_text,
                    prompt=prompt,
                    context=context,
                ),
                self._repair_learned_constraint_output(
                    provider,
                    prompt=prompt,
                    output=normalized_text,
                    route=route,
                    context=context,
                    tool_events=provider_response.tool_events,
                    verification_message=verification_result.message,
                    stream=stream,
                ),
            ):
                if candidate_text == normalized_text:
                    continue
                normalized_text = self._normalize_output(
                    route,
                    candidate_text,
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
                if verification_result.status == "pass":
                    learned_constraints_result = self._judge_learned_constraints(
                        provider,
                        prompt=prompt,
                        output=normalized_text,
                        route=route,
                        context=context,
                        tool_events=provider_response.tool_events,
                    )
                    if learned_constraints_result.status == "fail":
                        verification_result = learned_constraints_result
                if verification_result.status != "fail":
                    break
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
                    provider_response.tool_events,
                    stream=stream,
                    event_handler=event_handler,
                )
                retry_response = ProviderResponse(
                    text=retry_response.text,
                    usage=retry_response.usage,
                    raw=retry_response.raw,
                    tool_events=self._prepare_tool_events(retry_response.tool_events),
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
                if verification_result.status == "pass":
                    learned_constraints_result = self._judge_learned_constraints(
                        provider,
                        prompt=prompt,
                        output=normalized_text,
                        route=route,
                        context=context,
                        tool_events=provider_response.tool_events,
                    )
                    if learned_constraints_result.status == "fail":
                        verification_result = learned_constraints_result
                if verification_result.status == "fail" and verification_result.name == "learned_constraints_judge_v1":
                    for candidate_text in (
                        self._filter_output_by_teacher_terms(
                            normalized_text,
                            prompt=prompt,
                            context=context,
                        ),
                        self._repair_learned_constraint_output(
                            provider,
                            prompt=prompt,
                            output=normalized_text,
                            route=route,
                            context=context,
                            tool_events=provider_response.tool_events,
                            verification_message=verification_result.message,
                            stream=stream,
                        ),
                    ):
                        if candidate_text == normalized_text:
                            continue
                        normalized_text = self._normalize_output(
                            route,
                            candidate_text,
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
                        if verification_result.status == "pass":
                            learned_constraints_result = self._judge_learned_constraints(
                                provider,
                                prompt=prompt,
                                output=normalized_text,
                                route=route,
                                context=context,
                                tool_events=provider_response.tool_events,
                            )
                            if learned_constraints_result.status == "fail":
                                verification_result = learned_constraints_result
                        if verification_result.status != "fail":
                            break
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
            "selected_policies": selected_policies,
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
