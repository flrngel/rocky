from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from rocky.core.context import ContextBuilder
from rocky.core.messages import Message
from rocky.core.permissions import PermissionDenied
from rocky.core.router import Lane, RouteDecision, Router
from rocky.core.system_prompt import build_system_prompt
from rocky.core.verifiers import VerifierRegistry
from rocky.learning.manager import LearningManager
from rocky.providers.registry import ProviderRegistry
from rocky.session.store import SessionStore
from rocky.tools.registry import ToolRegistry
from rocky.util.text import safe_json
from rocky.util.time import utc_iso


@dataclass(slots=True)
class AgentResponse:
    text: str
    route: RouteDecision
    verification: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)


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
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.meta_handler = meta_handler
        self.last_prompt: str | None = None
        self.last_answer: str | None = None
        self.last_trace: dict[str, Any] | None = None
        self.last_context: dict[str, Any] | None = None

    def _trace_path(self) -> Path:
        stamp = utc_iso().replace(":", "").replace("-", "")
        return self.traces_dir / f"trace_{stamp}.json"

    def _write_trace(self, trace: dict[str, Any]) -> str:
        trace_path = self._trace_path()
        trace_path.write_text(safe_json(trace) + "\n", encoding="utf-8")
        return str(trace_path)

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

    def _finalize(
        self,
        session,
        prompt: str,
        text: str,
        route: RouteDecision,
        verification: dict[str, Any],
        usage: dict[str, Any],
        trace: dict[str, Any],
    ) -> AgentResponse:
        session.append("user", prompt)
        session.append("assistant", text)
        self.sessions.save(session)
        trace["trace_path"] = self._write_trace(trace)
        self.last_prompt = prompt
        self.last_answer = text
        self.last_trace = trace
        self.learning_manager.record_query(
            task_signature=route.task_signature,
            skills_used=trace.get("selected_skills") or [],
            verifier=verification.get("name", "default_v1"),
            result="success" if verification.get("status") == "pass" else verification.get("status", "warn"),
            usage=usage,
            latency_ms=None,
        )
        return AgentResponse(text=text, route=route, verification=verification, usage=usage, trace=trace)

    def run(
        self,
        prompt: str,
        stream: bool = False,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentResponse:
        session = self.sessions.ensure_current()
        route = self.router.route(prompt)
        if route.lane == Lane.META:
            text = self.meta_handler(prompt)
            if stream and event_handler and text:
                event_handler({"type": "assistant_chunk", "text": text})
            verification = {"name": "meta_v1", "status": "pass", "message": "Deterministic meta answer"}
            trace = {
                "route": asdict(route),
                "selected_tools": [],
                "selected_skills": [],
                "provider": "deterministic",
                "verification": verification,
                "tool_events": [],
                "context": {"instructions": [], "memories": [], "skills": [], "tool_families": []},
            }
            self.last_context = trace["context"]
            return self._finalize(session, prompt, text, route, verification, {}, trace)

        context = self.context_builder.build(prompt, route.task_signature, route.tool_families)
        context_summary = context.summary()
        self.last_context = context_summary
        system_prompt = build_system_prompt(context, self.permissions.config.mode)
        recent_messages = session.recent_messages(limit=12)
        messages = [*recent_messages, Message(role="user", content=prompt)]
        provider = self.provider_registry.provider_for_task(needs_tools=bool(route.tool_families))
        selected_tools = [tool.name for tool in self.tool_registry.select(route.tool_families)]
        selected_skills = [item["name"] for item in context.skills]

        if route.tool_families:
            provider_response = provider.run_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=self.tool_registry.get_openai_schemas(route.tool_families),
                execute_tool=lambda name, arguments: self._run_tool(name, arguments, event_handler=event_handler),
                max_rounds=10 if route.lane == Lane.DEEP else 6,
                event_handler=event_handler if stream else None,
            )
        else:
            provider_response = provider.complete(
                system_prompt=system_prompt,
                messages=messages,
                stream=stream,
                event_handler=event_handler,
            )

        verification_result = self.verifier_registry.verify(
            prompt=prompt,
            task_class=route.task_class,
            output=provider_response.text,
            tool_events=provider_response.tool_events,
        )
        verification = {
            "name": verification_result.name,
            "status": verification_result.status,
            "message": verification_result.message,
        }
        trace = {
            "route": asdict(route),
            "selected_tools": selected_tools,
            "selected_skills": selected_skills,
            "provider": provider.__class__.__name__,
            "verification": verification,
            "usage": provider_response.usage,
            "tool_events": provider_response.tool_events,
            "context": context_summary,
            "raw_provider_keys": sorted(provider_response.raw.keys()),
        }
        return self._finalize(
            session=session,
            prompt=prompt,
            text=provider_response.text,
            route=route,
            verification=verification,
            usage=provider_response.usage,
            trace=trace,
        )
