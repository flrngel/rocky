from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from rocky.commands.registry import CommandRegistry
from rocky.config.loader import ConfigLoader
from rocky.core.agent import AgentCore, AgentResponse
from rocky.core.context import ContextBuilder
from rocky.core.permissions import PermissionManager
from rocky.core.router import Router
from rocky.core.verifiers import VerifierRegistry
from rocky.learning.manager import LearningManager
from rocky.memory.retriever import MemoryRetriever
from rocky.memory.store import MemoryStore
from rocky.providers.registry import ProviderRegistry
from rocky.session.store import SessionStore
from rocky.skills.loader import SkillLoader
from rocky.skills.retriever import SkillRetriever
from rocky.tools.base import ToolContext
from rocky.tools.registry import ToolRegistry
from rocky.util.io import write_text
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
        self.commands = CommandRegistry(self)

    @classmethod
    def load_from(
        cls,
        cwd: Path | None = None,
        cli_overrides: dict[str, Any] | None = None,
    ) -> "RockyRuntime":
        cwd = (cwd or Path.cwd()).resolve()
        workspace = discover_workspace(cwd)
        workspace.ensure_layout()
        global_root = ensure_global_layout()
        config = ConfigLoader(global_root, workspace.root).load(cli_overrides)
        permissions = PermissionManager(config.permissions, workspace.root)
        sessions = SessionStore(workspace.sessions_dir)
        sessions.ensure_current()
        bundled_root = Path(__file__).resolve().parent / "data" / "bundled_skills"
        skill_loader = SkillLoader(workspace.root, global_root, bundled_root)
        skill_retriever = SkillRetriever(skill_loader.load_all())
        memory_store = MemoryStore(workspace.memories_dir, global_root / "memories")
        memory_retriever = MemoryRetriever(memory_store.load_all())
        instruction_candidates = workspace.instruction_candidates + [global_root / "AGENTS.md"]
        context_builder = ContextBuilder(
            workspace.root,
            instruction_candidates,
            skill_retriever,
            memory_retriever,
        )
        tool_context = ToolContext(workspace.root, workspace.artifacts_dir, permissions, config)
        tool_registry = ToolRegistry(tool_context)
        provider_registry = ProviderRegistry(config)
        learning_manager = LearningManager(
            support_dir=workspace.episodes_support_dir,
            query_dir=workspace.episodes_query_dir,
            learned_root=workspace.skills_learned_dir,
            artifacts_dir=workspace.artifacts_dir,
            policies_dir=workspace.policies_dir,
            config=config.learning,
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
        )
        agent.meta_handler = runtime.meta_answer
        return runtime

    def refresh_knowledge(self) -> None:
        self.skill_retriever = SkillRetriever(self.skill_loader.load_all())
        self.memory_retriever = MemoryRetriever(self.memory_store.load_all())
        instruction_candidates = self.workspace.instruction_candidates + [self.global_root / "AGENTS.md"]
        self.context_builder = ContextBuilder(
            self.workspace.root,
            instruction_candidates,
            self.skill_retriever,
            self.memory_retriever,
        )
        self.agent.context_builder = self.context_builder

    def reload_config(self, cli_overrides: dict[str, Any] | None = None) -> None:
        config = ConfigLoader(self.global_root, self.workspace.root).load(cli_overrides)
        self.config = config
        self.permissions.config = config.permissions
        self.tool_registry.context.config = config
        self.provider_registry.config = config
        self.learning_manager.config = config.learning

    def run_prompt(self, prompt: str, stream: bool = False, event_handler=None) -> AgentResponse:
        return self.agent.run(prompt, stream=stream, event_handler=event_handler)

    def meta_answer(self, prompt: str) -> str:
        lowered = prompt.lower()
        if "tool" in lowered:
            return dump_yaml({"tools": self.tool_registry.list_tools()})
        if "skill" in lowered:
            return dump_yaml({"skills": self.skill_inventory()})
        if "config" in lowered:
            return dump_yaml(self.config_dict())
        if "permission" in lowered:
            return dump_yaml(self.permissions.explain())
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
        current = self.sessions.ensure_current()
        return {
            "workspace_root": str(self.workspace.root),
            "session_id": current.id,
            "active_provider": self.config.active_provider,
            "permission_mode": self.config.permissions.mode,
            "skills": len(self.skill_retriever.skills),
            "memories": len(self.memory_retriever.notes),
            "learned_generation": self.learning_manager.current_generation(),
        }

    def current_context(self) -> dict[str, Any]:
        return self.agent.last_context or {"instructions": [], "memories": [], "skills": [], "tool_families": []}

    def why(self) -> dict[str, Any]:
        return self.agent.last_trace or {"status": "No task has been run yet."}

    def last_trace(self) -> dict[str, Any]:
        return self.agent.last_trace or {"status": "No task has been run yet."}

    def skill_inventory(self) -> list[dict[str, Any]]:
        return self.skill_retriever.inventory()

    def memory_inventory(self) -> list[dict[str, Any]]:
        return self.memory_retriever.inventory()

    def new_session(self, title: str = "session") -> dict[str, Any]:
        session = self.sessions.create(title=title)
        return {"created": True, "session_id": session.id, "title": session.title}

    def resume_session(self, session_id: str | None = None) -> dict[str, Any]:
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
        self.config.permissions.mode = "plan" if enabled else "supervised"
        self.permissions.config.mode = self.config.permissions.mode
        return {"permission_mode": self.config.permissions.mode}

    def learn(self, feedback: str) -> dict[str, Any]:
        if not feedback.strip():
            return {"published": False, "reason": "feedback is required"}
        if not self.agent.last_prompt or not self.agent.last_answer or not self.agent.last_trace:
            return {"published": False, "reason": "no previous answer to learn from"}
        result = self.learning_manager.learn_from_feedback(
            task_signature=self.agent.last_trace["route"]["task_signature"],
            prompt=self.agent.last_prompt,
            answer=self.agent.last_answer,
            feedback=feedback,
            trace=self.agent.last_trace,
            scope="project",
        )
        self.refresh_knowledge()
        return result

    def undo(self) -> dict[str, Any]:
        result = self.learning_manager.rollback_latest() or {"rolled_back": False, "reason": "no learned skills found"}
        self.refresh_knowledge()
        return result

    def doctor(self) -> dict[str, Any]:
        provider_ok, provider_message = self.provider_registry.healthcheck()
        return {
            "provider": {"ok": provider_ok, "message": provider_message},
            "workspace": str(self.workspace.root),
            "current_session": self.sessions.ensure_current().id,
            "tool_count": len(self.tool_registry.tools),
            "skill_count": len(self.skill_retriever.skills),
            "memory_count": len(self.memory_retriever.notes),
        }

    def init_scaffold(self) -> dict[str, Any]:
        self.workspace.ensure_layout()
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
        if not self.workspace.config_path.exists():
            write_text(self.workspace.config_path, dump_yaml({"permissions": {"mode": "supervised"}}))
        return {"initialized": True, "workspace_root": str(self.workspace.root)}
