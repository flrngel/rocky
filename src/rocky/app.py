from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from rocky import __version__
from rocky.commands.registry import CommandRegistry
from rocky.config.loader import ConfigLoader
from rocky.core.agent import AgentCore, AgentResponse
from rocky.core.context import ContextBuilder
from rocky.core.permissions import PermissionManager
from rocky.core.router import Lane, Router
from rocky.core.runtime_state import ThreadRegistry
from rocky.core.verifiers import VerifierRegistry
from rocky.harness import harness_inventory as harness_catalog
from rocky.learning.manager import LearningManager
from rocky.memory.retriever import MemoryRetriever
from rocky.memory.store import MemoryStore
from rocky.providers.registry import ProviderRegistry
from rocky.session.store import SessionStore
from rocky.skills.loader import SkillLoader
from rocky.skills.retriever import SkillRetriever
from rocky.student.store import StudentStore
from rocky.tools.base import ToolContext
from rocky.tools.registry import ToolRegistry
from rocky.util.io import read_yaml, write_text
from rocky.util.paths import WorkspacePaths, discover_workspace, ensure_global_layout
from rocky.util.yamlx import dump_yaml


class RockyRuntime:
    def __init__(
        self,
        workspace: WorkspacePaths,
        global_root: Path,
        config,
        permissions: PermissionManager,
        sessions: SessionStore,
        memory_store: MemoryStore,
        memory_retriever: MemoryRetriever,
        skill_loader: SkillLoader,
        skill_retriever: SkillRetriever,
        context_builder: ContextBuilder,
        tool_registry: ToolRegistry,
        provider_registry: ProviderRegistry,
        learning_manager: LearningManager,
        agent: AgentCore,
        student_store: StudentStore,
        *,
        freeze_enabled: bool = False,
        verbose_enabled: bool = False,
    ) -> None:
        self.workspace = workspace
        self.global_root = global_root
        self.config = config
        self.permissions = permissions
        self.sessions = sessions
        self.memory_store = memory_store
        self.memory_retriever = memory_retriever
        self.skill_loader = skill_loader
        self.skill_retriever = skill_retriever
        self.context_builder = context_builder
        self.tool_registry = tool_registry
        self.provider_registry = provider_registry
        self.learning_manager = learning_manager
        self.agent = agent
        self.student_store = student_store
        self.freeze_enabled = freeze_enabled
        self.verbose_enabled = verbose_enabled
        self.freeze_session_seed = sessions.peek_current() if freeze_enabled else None
        self.commands = CommandRegistry(self)

    @classmethod
    def load_from(
        cls,
        cwd: Path | None = None,
        cli_overrides: dict[str, Any] | None = None,
        *,
        freeze: bool = False,
        verbose: bool = False,
    ) -> "RockyRuntime":
        cwd = (cwd or Path.cwd()).resolve()
        workspace = discover_workspace(cwd)
        if not freeze:
            workspace.ensure_layout()
        global_root = ensure_global_layout(create_layout=not freeze)
        config = ConfigLoader(global_root, workspace.root).load(cli_overrides, create_defaults=not freeze)
        permissions = PermissionManager(config.permissions, workspace.root)
        sessions = SessionStore(workspace.sessions_dir, create_layout=not freeze)
        if not freeze:
            sessions.ensure_current()
        bundled_root = Path(__file__).resolve().parent / "data" / "bundled_skills"
        skill_loader = SkillLoader(workspace.root, global_root, bundled_root)
        skill_retriever = SkillRetriever(skill_loader.load_all())
        memory_store = MemoryStore(workspace.memories_dir, global_root / "memories", create_layout=not freeze)
        memory_retriever = MemoryRetriever(memory_store.load_all())
        student_store = StudentStore(workspace.student_dir, create_layout=not freeze)
        instruction_candidates = workspace.instruction_candidates + [global_root / "AGENTS.md"]
        context_builder = ContextBuilder(
            workspace.root,
            workspace.execution_root,
            instruction_candidates,
            skill_retriever,
            memory_retriever,
            sessions,
            student_store,
        )
        tool_context = ToolContext(
            workspace.root,
            workspace.execution_root,
            workspace.artifacts_dir,
            permissions,
            config,
        )
        tool_registry = ToolRegistry(tool_context)
        provider_registry = ProviderRegistry(config)
        learning_manager = LearningManager(
            support_dir=workspace.episodes_support_dir,
            query_dir=workspace.episodes_query_dir,
            learned_root=workspace.skills_learned_dir,
            artifacts_dir=workspace.artifacts_dir,
            policies_dir=workspace.policies_dir,
            config=config.learning,
            create_layout=not freeze,
        )
        agent = AgentCore(
            router=Router(),
            sessions=sessions,
            context_builder=context_builder,
            tool_registry=tool_registry,
            provider_registry=provider_registry,
            verifier_registry=VerifierRegistry(),
            learning_manager=learning_manager,
            permissions=permissions,
            traces_dir=workspace.traces_dir,
            meta_handler=lambda prompt: "",
            create_layout=not freeze,
        )
        runtime = cls(
            workspace=workspace,
            global_root=global_root,
            config=config,
            permissions=permissions,
            sessions=sessions,
            memory_store=memory_store,
            memory_retriever=memory_retriever,
            skill_loader=skill_loader,
            skill_retriever=skill_retriever,
            context_builder=context_builder,
            tool_registry=tool_registry,
            provider_registry=provider_registry,
            learning_manager=learning_manager,
            agent=agent,
            student_store=student_store,
            freeze_enabled=freeze,
            verbose_enabled=verbose,
        )
        agent.meta_handler = runtime.meta_answer
        return runtime

    def refresh_knowledge(self) -> None:
        self.skill_retriever = SkillRetriever(self.skill_loader.load_all())
        self.memory_retriever = MemoryRetriever(self.memory_store.load_all())
        instruction_candidates = self.workspace.instruction_candidates + [self.global_root / "AGENTS.md"]
        self.context_builder = ContextBuilder(
            self.workspace.root,
            self.workspace.execution_root,
            instruction_candidates,
            self.skill_retriever,
            self.memory_retriever,
            self.sessions,
            self.student_store,
        )
        self.agent.context_builder = self.context_builder

    def reload_config(self, cli_overrides: dict[str, Any] | None = None) -> None:
        config = ConfigLoader(self.global_root, self.workspace.root).load(cli_overrides)
        self.config = config
        self.permissions.config = config.permissions
        self.tool_registry.context.config = config
        self.provider_registry.config = config
        self.learning_manager.config = config.learning

    def run_prompt(
        self,
        prompt: str,
        stream: bool = False,
        event_handler=None,
        continue_session: bool = True,
        freeze: bool | None = None,
    ) -> AgentResponse:
        effective_freeze = self.freeze_enabled if freeze is None else freeze
        response = self.agent.run(
            prompt,
            stream=stream,
            event_handler=event_handler,
            continue_session=continue_session,
            freeze=effective_freeze,
            session_seed=self.freeze_session_seed if effective_freeze else None,
        )
        if not effective_freeze and self._should_capture_project_memory(response):
            try:
                current_thread = ((response.trace.get("thread") or {}).get("current_thread") or {})
                result = self.memory_store.capture_project_memory(
                    prompt=prompt,
                    answer=response.text,
                    task_signature=str(current_thread.get("task_signature") or response.route.task_signature),
                    trace=response.trace,
                    supported_claims=response.trace.get("supported_claims") or [],
                    thread_id=str(current_thread.get("thread_id") or "") or None,
                )
                if result.get("written"):
                    self.refresh_knowledge()
            except Exception:
                pass
        return response

    def _should_capture_project_memory(self, response: AgentResponse) -> bool:
        if response.verification.get("status") != "pass":
            return False
        if response.route.lane == Lane.META:
            return False
        if response.verification.get("memory_promotion_allowed") is False:
            return False
        return True

    def harness_inventory(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "execution_cwd": self.workspace.execution_relative,
            **harness_catalog(),
        }

    def meta_answer(self, prompt: str) -> str:
        lowered = prompt.lower()
        if "provider" in lowered or "model" in lowered:
            provider_name = self.config.active_provider
            provider = self.config.provider(provider_name)
            return dump_yaml(
                {
                    "active_provider": provider_name,
                    "model": provider.model,
                    "base_url": provider.base_url,
                    "style": provider.style,
                }
            )
        if "tool" in lowered:
            return dump_yaml({"tools": self.tool_registry.list_tools()})
        if "skill" in lowered:
            return dump_yaml({"skills": self.skill_inventory()})
        if "harness" in lowered or "phase" in lowered:
            return dump_yaml(self.harness_inventory())
        if "config" in lowered:
            return dump_yaml(self.config_dict())
        if "permission" in lowered:
            return dump_yaml(self.permissions.explain())
        if "student" in lowered or "teach" in lowered:
            return dump_yaml(self.student_status())
        if "thread" in lowered:
            return dump_yaml(self.thread_inventory())
        if "memory" in lowered:
            return dump_yaml({"memory": self.memory_inventory()})
        if "status" in lowered:
            return dump_yaml(self.status())
        if "session" in lowered:
            return dump_yaml({"sessions": self.sessions.list()})
        return "Rocky is ready. Use /help for controls or ask for work directly."

    def config_dict(self) -> dict[str, Any]:
        return {
            "active_provider": self.config.active_provider,
            "providers": {name: asdict(cfg) for name, cfg in self.config.providers.items()},
            "permissions": asdict(self.config.permissions),
            "tools": asdict(self.config.tools),
            "learning": asdict(self.config.learning),
        }

    def status(self) -> dict[str, Any]:
        current = self._status_session()
        provider = self.config.provider(self.config.active_provider)
        return {
            "version": __version__,
            "workspace_root": str(self.workspace.root),
            "execution_root": str(self.workspace.execution_root),
            "execution_cwd": self.workspace.execution_relative,
            "session_id": current.id if current is not None else None,
            "runtime": {
                "active_provider": self.config.active_provider,
                "model": provider.model,
                "base_url": provider.base_url,
                "style": provider.style,
                "tool_permission_enforcement": "disabled",
                "legacy_permission_mode": self.config.permissions.mode,
                "freeze_mode": self.freeze_enabled,
                "verbose_mode": self.verbose_enabled,
            },
            "skills": len(self.skill_retriever.skills),
            "memories": len(self.memory_retriever.notes),
            "student": self.student_store.status(),
            "learned_generation": self.learning_manager.current_generation(),
            "global_settings": self._config_source_snapshot("global", self.global_root / "config.yaml"),
            "project_settings": {
                "project": self._config_source_snapshot("project", self.workspace.config_path),
                "local": self._config_source_snapshot("local", self.workspace.config_local_path),
            },
            "effective_settings": self.config_dict(),
        }

    def current_context(self) -> dict[str, Any]:
        return self.agent.last_context or {
            "instructions": [],
            "memories": [],
            "skills": [],
            "tool_families": [],
            "workspace_focus": {
                "workspace_root": str(self.workspace.root),
                "execution_cwd": self.workspace.execution_relative,
            },
            "handoffs": [],
            "student_profile": {},
            "student_notes": [],
        }

    def why(self) -> dict[str, Any]:
        return self.agent.last_trace or {"status": "No task has been run yet."}

    def last_trace(self) -> dict[str, Any]:
        return self.agent.last_trace or {"status": "No task has been run yet."}

    def skill_inventory(self) -> list[dict[str, Any]]:
        return self.skill_retriever.inventory()

    def memory_inventory(self) -> list[dict[str, Any]]:
        return self.memory_store.inventory()

    def thread_inventory(self) -> dict[str, Any]:
        current = self._status_session()
        registry = ThreadRegistry(current) if current is not None else None
        if registry is None:
            return {"current_thread_id": None, "threads": []}
        return {
            "current_thread_id": registry.current_thread_id,
            "threads": registry.thread_summary_records(limit=20),
        }

    def student_status(self) -> dict[str, Any]:
        return self.student_store.status()

    def student_inventory(self, kind: str | None = None) -> dict[str, Any]:
        return self.student_store.inventory(kind)

    def student_show(self, entry_id: str) -> dict[str, Any]:
        note = self.student_store.get(entry_id)
        if note is None:
            return {"ok": False, "reason": f"student note not found: {entry_id}"}
        return {"ok": True, "student": note}

    def student_add(self, kind: str, title: str, text: str) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("student add")
        result = self.student_store.add(kind, title, text)
        if result.get("ok"):
            self.refresh_knowledge()
        return result

    def memory_list(self) -> dict[str, Any]:
        rows = self.memory_inventory()
        return {
            "project_auto": [row for row in rows if row.get("scope") == "project_auto"],
            "global_manual": [row for row in rows if row.get("scope") == "global_manual"],
        }

    def memory_show(self, scope: str, name: str) -> dict[str, Any]:
        note = self.memory_store.get_note(scope, name)
        if note is None:
            return {"ok": False, "reason": f"memory not found: {scope}:{name}"}
        return {
            "ok": True,
            "memory": {
                **note.as_record(),
                "text": note.text,
                "source_task_signature": note.source_task_signature,
                "evidence_excerpt": note.evidence_excerpt,
                "fingerprint": note.fingerprint,
            },
        }

    def memory_add(self, name: str, text: str) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("memory add")
        result = self.memory_store.add_global_manual(name, text)
        if result.get("ok"):
            self.refresh_knowledge()
        return result

    def memory_set(self, name: str, text: str) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("memory set")
        result = self.memory_store.set_global_manual(name, text)
        if result.get("ok"):
            self.refresh_knowledge()
        return result

    def memory_remove(self, name: str) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("memory remove")
        result = self.memory_store.remove_global_manual(name)
        if result.get("ok"):
            self.refresh_knowledge()
        return result

    def new_session(self, title: str = "session") -> dict[str, Any]:
        if self.freeze_enabled:
            session = self.sessions.create_ephemeral(title=title)
            self.freeze_session_seed = session
            return {"created": True, "session_id": session.id, "title": session.title, "ephemeral": True}
        session = self.sessions.create(title=title)
        return {"created": True, "session_id": session.id, "title": session.title}

    def resume_session(self, session_id: str | None = None) -> dict[str, Any]:
        if self.freeze_enabled:
            if session_id:
                session = self.sessions.load_snapshot(session_id)
                self.freeze_session_seed = session
                return {"resumed": True, "session_id": session.id, "title": session.title, "ephemeral": True}
            rows = self.sessions.list()
            if not rows:
                session = self.sessions.create_ephemeral()
                self.freeze_session_seed = session
                return {"resumed": True, "session_id": session.id, "title": session.title, "ephemeral": True}
            session = self.sessions.load_snapshot(rows[0]["id"])
            self.freeze_session_seed = session
            return {"resumed": True, "session_id": session.id, "title": session.title, "ephemeral": True}
        if session_id:
            session = self.sessions.load(session_id)
            return {"resumed": True, "session_id": session.id, "title": session.title}
        rows = self.sessions.list()
        if not rows:
            session = self.sessions.create()
            return {"resumed": True, "session_id": session.id, "title": session.title}
        session = self.sessions.load(rows[0]["id"])
        return {"resumed": True, "session_id": session.id, "title": session.title}

    def set_plan_mode(self, enabled: bool) -> dict[str, Any]:
        self.config.permissions.mode = "plan" if enabled else "bypass"
        self.permissions.config.mode = self.config.permissions.mode
        return {
            "plan_mode": enabled,
            "tool_permission_enforcement": "disabled",
            "legacy_permission_mode": self.config.permissions.mode,
        }

    def _last_turn_from_session(self, session) -> tuple[str | None, str | None]:
        prompt: str | None = None
        answer: str | None = None
        for row in reversed(session.messages):
            role = str(row.get("role") or "").strip()
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            if answer is None:
                if role == "assistant":
                    answer = content
                continue
            if role == "user":
                prompt = content
                break
        return prompt, answer

    def _load_trace_payload(self, raw_path: str | None) -> dict[str, Any] | None:
        if not raw_path:
            return None
        candidate = Path(raw_path)
        candidates = [candidate]
        if not candidate.is_absolute():
            candidates.append(self.workspace.root / candidate)
            candidates.append(self.workspace.traces_dir / candidate.name)
        for path in candidates:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        return None

    def _recover_trace_for_session(
        self,
        session,
        *,
        prompt: str,
    ) -> dict[str, Any] | None:
        meta = session.meta or {}
        last_turn = meta.get("last_turn_summary") or {}
        last_thread = meta.get("last_thread_summary") or {}
        for raw_path in (
            meta.get("last_trace_path"),
            last_turn.get("trace_path"),
            last_thread.get("trace_path"),
        ):
            trace = self._load_trace_payload(str(raw_path) if raw_path else None)
            if trace is not None:
                return trace

        thread_id = str(last_turn.get("thread_id") or meta.get("last_thread_id") or last_thread.get("thread_id") or "").strip()
        task_signature = str(last_turn.get("task_signature") or meta.get("last_task_signature") or "").strip()
        for path in sorted(self.workspace.traces_dir.glob("trace_*.json"), reverse=True)[:48]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            current_thread = ((payload.get("thread") or {}).get("current_thread") or {})
            route = payload.get("route") or {}
            if thread_id and str(current_thread.get("thread_id") or "") == thread_id:
                return payload
            prompt_history = current_thread.get("prompt_history") or []
            last_prompt = str(prompt_history[-1].get("prompt") or "").strip() if prompt_history else ""
            if task_signature and str(route.get("task_signature") or "") == task_signature and last_prompt == prompt:
                return payload
        return None

    def _synthetic_trace_for_session(self, session) -> dict[str, Any] | None:
        meta = session.meta or {}
        last_turn = meta.get("last_turn_summary") or {}
        last_thread = meta.get("last_thread_summary") or {}
        task_signature = str(last_turn.get("task_signature") or meta.get("last_task_signature") or "").strip()
        if not task_signature:
            return None
        verification_status = str(last_turn.get("verification") or meta.get("last_verification") or "pass").strip() or "pass"
        thread_id = str(last_turn.get("thread_id") or meta.get("last_thread_id") or "").strip()
        registry = meta.get("task_threads_v1_0") or meta.get("task_threads_v0_3") or {}
        current_thread = dict((registry.get(thread_id) or {}).get("thread") or {}) if thread_id else {}
        if current_thread and not current_thread.get("summary_text"):
            current_thread["summary_text"] = str(last_thread.get("text") or "")
        return {
            "route": {
                "lane": "standard",
                "task_class": task_signature.split("/", 1)[0] or "repo",
                "risk": "medium",
                "reasoning": "Recovered from persisted session history",
                "tool_families": [],
                "task_signature": task_signature,
                "confidence": 0.0,
                "source": "session_recovery",
                "continued_thread_id": None,
                "continuation_decision": "resume_recent_thread" if current_thread else "start_new_thread",
            },
            "verification": {
                "name": "session_recovery_v1",
                "status": verification_status,
                "message": "Recovered from persisted session history",
                "failure_class": None,
            },
            "selected_tools": list(last_turn.get("tool_names") or []),
            "thread": {"current_thread": current_thread} if current_thread else {},
            "tool_events": [],
            "context": {},
        }

    def _restore_recent_agent_state(self) -> bool:
        if self.agent.last_prompt and self.agent.last_answer and self.agent.last_trace:
            return True
        candidate_ids: list[str] = []
        seen: set[str] = set()
        for row in self.sessions.list():
            session_id = str(row.get("id") or "").strip()
            if session_id and session_id not in seen:
                candidate_ids.append(session_id)
                seen.add(session_id)
        current = self.sessions.peek_current()
        if current is not None and current.id not in seen:
            candidate_ids.append(current.id)
        for session_id in candidate_ids[:12]:
            try:
                session = self.sessions.load_snapshot(session_id)
            except Exception:
                continue
            prompt, answer = self._last_turn_from_session(session)
            if not prompt or not answer:
                continue
            trace = self._recover_trace_for_session(session, prompt=prompt) or self._synthetic_trace_for_session(session)
            if trace is None:
                continue
            self.agent.last_prompt = prompt
            self.agent.last_answer = answer
            self.agent.last_trace = trace
            return True
        return False

    def learn(self, feedback: str) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("learn", key="published")
        if not feedback.strip():
            return {"published": False, "reason": "feedback is required"}
        self._restore_recent_agent_state()
        if not self.agent.last_prompt or not self.agent.last_answer or not self.agent.last_trace:
            return {"published": False, "reason": "no previous answer to learn from"}
        thread_snapshot = ((self.agent.last_trace.get("thread") or {}).get("current_thread") or {})
        task_signature = str(thread_snapshot.get("task_signature") or self.agent.last_trace["route"]["task_signature"])
        task_family = str(thread_snapshot.get("task_family") or task_signature.split("/", 1)[0])
        thread_id = str(thread_snapshot.get("thread_id") or "") or None
        failure_class = self.agent.last_trace.get("verification", {}).get("failure_class")
        provider = None
        try:
            provider = self.provider_registry.primary()
        except Exception:
            provider = None
        analysis = self.learning_manager.analyze_feedback(
            task_signature=task_signature,
            prompt=self.agent.last_prompt,
            answer=self.agent.last_answer,
            feedback=feedback,
            trace=self.agent.last_trace,
            provider=provider,
            task_family=task_family,
            thread_id=thread_id,
            failure_class=str(failure_class) if failure_class else None,
        )
        note_result = self.student_store.record_feedback(
            feedback,
            prompt=self.agent.last_prompt,
            answer=self.agent.last_answer,
            task_signature=task_signature,
            thread_id=thread_id,
            failure_class=analysis.failure_class,
        )
        structured_memory_result: dict[str, Any] | None = None
        if analysis.memory_kind in {"pattern", "example"}:
            structured_memory_result = self.student_store.add(
                analysis.memory_kind,
                analysis.title,
                analysis.memory_text(),
                prompt=self.agent.last_prompt,
                answer=self.agent.last_answer,
                feedback=feedback,
                task_signature=task_signature,
                thread_id=thread_id,
                failure_class=analysis.failure_class,
                tags=analysis.triggers[:8],
                origin="learned_feedback",
            )
        result = self.learning_manager.learn_from_feedback(
            task_signature=task_signature,
            prompt=self.agent.last_prompt,
            answer=self.agent.last_answer,
            feedback=feedback,
            trace=self.agent.last_trace,
            scope="project",
            thread_id=thread_id,
            task_family=task_family,
            failure_class=analysis.failure_class,
            analysis=analysis,
            provider=provider,
        )
        result["memory_kind"] = analysis.memory_kind
        result["reflection_source"] = analysis.reflection_source
        result["analysis"] = analysis.as_record()
        result["student"] = note_result.get("entry")
        result["student_memory"] = structured_memory_result.get("entry") if structured_memory_result else None
        result["student_pattern"] = (
            structured_memory_result.get("entry")
            if structured_memory_result and analysis.memory_kind == "pattern"
            else None
        )
        self.refresh_knowledge()
        return result

    def teach(self, feedback: str) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("teach", key="ok")
        if not feedback.strip():
            return {"ok": False, "reason": "feedback is required"}
        self._restore_recent_agent_state()
        if self.agent.last_prompt and self.agent.last_answer and self.agent.last_trace:
            result = self.learn(feedback)
            result["teachable"] = True
            return result
        note_result = self.student_store.record_feedback(feedback)
        self.refresh_knowledge()
        return {
            "ok": bool(note_result.get("ok")),
            "published": False,
            "teachable": True,
            "student": note_result.get("entry"),
            "reason": note_result.get("reason") or None,
        }

    def undo(self) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("undo", key="rolled_back")
        result = self.learning_manager.rollback_latest() or {"rolled_back": False, "reason": "no learned skills found"}
        self.refresh_knowledge()
        return result

    def doctor(self) -> dict[str, Any]:
        provider_ok, provider_message = self.provider_registry.healthcheck()
        current = self._status_session()
        return {
            "provider": {"ok": provider_ok, "message": provider_message},
            "workspace": str(self.workspace.root),
            "current_session": current.id if current is not None else None,
            "freeze_mode": self.freeze_enabled,
            "tool_count": len(self.tool_registry.tools),
            "skill_count": len(self.skill_retriever.skills),
            "memory_count": len(self.memory_retriever.notes),
            "student_count": self.student_store.status().get("count", 0),
        }

    def compact_session(self) -> dict[str, Any]:
        if self.freeze_enabled:
            return self._freeze_blocked_result("compact", key="compacted")
        return self.sessions.compact()

    def set_freeze_mode(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            if not self.freeze_enabled or self.freeze_session_seed is None:
                self.freeze_session_seed = self._snapshot_session_seed()
            self.freeze_enabled = True
        else:
            self.freeze_enabled = False
            self.freeze_session_seed = None
        return {
            "freeze_mode": self.freeze_enabled,
            "session_id": self.freeze_session_seed.id if self.freeze_session_seed is not None else None,
        }

    def freeze_status(self) -> dict[str, Any]:
        current = self._status_session()
        return {
            "freeze_mode": self.freeze_enabled,
            "session_id": current.id if current is not None else None,
        }

    def _snapshot_session_seed(self):
        if self.sessions.current is not None:
            return self.sessions.current.clone()
        return self.sessions.peek_current()

    def _status_session(self):
        if self.freeze_enabled:
            if self.freeze_session_seed is not None:
                return self.freeze_session_seed
            return self.sessions.peek_current()
        return self.sessions.ensure_current()

    def _freeze_blocked_result(self, action: str, *, key: str = "ok") -> dict[str, Any]:
        return {
            key: False,
            "reason": f"Freeze mode is enabled; {action} is disabled because it would write Rocky state.",
            "freeze_mode": True,
        }

    def _config_source_snapshot(self, scope: str, path: Path) -> dict[str, Any]:
        data = read_yaml(path)
        return {
            "scope": scope,
            "path": str(path),
            "exists": path.exists(),
            "values": data if isinstance(data, dict) else None,
        }

    def init_scaffold(self) -> dict[str, Any]:
        self.workspace.ensure_layout()
        self.student_store.ensure_layout()
        if not (self.workspace.root / "AGENTS.md").exists():
            write_text(
                self.workspace.root / "AGENTS.md",
                "# Project instructions\n\nDescribe project goals, constraints, and norms here.\n",
            )
        if not (self.workspace.root / "ROCKY.md").exists():
            write_text(
                self.workspace.root / "ROCKY.md",
                "# Rocky workspace note\n\nAdd operator notes that Rocky should load at startup.\n",
            )
        return {
            "initialized": True,
            "workspace_root": str(self.workspace.root),
            "student_root": str(self.workspace.student_dir),
        }
