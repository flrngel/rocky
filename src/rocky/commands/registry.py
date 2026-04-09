from __future__ import annotations

from dataclasses import dataclass, field
import shlex
from typing import TYPE_CHECKING, Any

from rocky.util.yamlx import dump_yaml

if TYPE_CHECKING:
    from rocky.app import RockyRuntime


@dataclass(slots=True)
class CommandResult:
    name: str
    text: str
    data: Any = None


@dataclass(slots=True)
class CommandRegistry:
    runtime: "RockyRuntime"
    aliases: dict[str, str] = field(
        default_factory=lambda: {
            "setup": "init",
            "set-up": "init",
        }
    )
    names: list[str] = field(
        default_factory=lambda: [
            "help",
            "tools",
            "skills",
            "harness",
            "memory",
            "student",
            "threads",
            "teach",
            "learned",
            "policies",
            "permissions",
            "context",
            "status",
            "sessions",
            "resume",
            "new",
            "config",
            "doctor",
            "why",
            "compact",
            "freeze",
            "plan",
            "learn",
            "undo",
            "init",
            "trace",
            "configure",
            "setup",
            "set-up",
        ]
    )

    def handle(self, line: str) -> CommandResult:
        stripped = line.strip()
        if stripped.startswith("/"):
            stripped = stripped[1:]
        if not stripped:
            return CommandResult("help", self._help_text())
        if stripped.startswith("learn "):
            feedback = stripped[len("learn ") :].strip()
            data = self.runtime.learn(feedback)
            return CommandResult("learn", dump_yaml(data), data)
        if stripped.startswith("teach "):
            feedback = stripped[len("teach ") :].strip()
            data = self.runtime.teach(feedback)
            return CommandResult("teach", dump_yaml(data), data)
        parts = shlex.split(stripped)
        raw_name = parts[0]
        name = self.aliases.get(raw_name, raw_name)
        args = parts[1:]
        method = getattr(self, f"cmd_{name.replace('-', '_')}", None)
        if method is None:
            return CommandResult("error", f"Unknown command: /{raw_name}\n\n{self._help_text()}")
        return method(args)

    def _help_text(self) -> str:
        return "\n".join(
            [
                "# Rocky commands",
                "- `/help` show commands",
                "- `/tools` list tools",
                "- `/skills` list authored agent skills",
                "- `/harness` show the installed-CLI agentic scenario playbook",
                "- `/memory` list project/global memory notes",
                "- `/memory show <scope>:<name>` show one memory note",
                "- `/memory add <name> <text>` add global manual memory",
                "- `/memory set <name> <text>` create or replace global manual memory",
                "- `/memory remove <name>` remove global manual memory",
                "- `/student` show student notebook status",
                "- `/student list [kind]` list notebook entries",
                "- `/student show <entry_id>` show one student note",
                "- `/student add <kind> <title> <text>` add knowledge, pattern, or example",
                "- `/threads` show active and recent task threads",
                "- `/teach <feedback>` write durable teacher feedback to Rocky's notebook",
                "- `/learned` list learned policies",
                "- `/policies` list learned policies",
                "- `/permissions` show legacy permission config (tool blocking is disabled)",
                "- `/context` show last assembled context",
                "- `/status` show runtime status",
                "- `/sessions` list sessions",
                "- `/resume [session_id]` resume a session",
                "- `/new [title]` create a new session",
                "- `/config` show effective config",
                "- `/configure` run the global config wizard",
                "- `/doctor` run basic health checks",
                "- `/why` show last routing trace",
                "- `/trace` show last full trace",
                "- `/compact` compact current session history",
                "- `/freeze` toggle freeze mode for this process",
                "- `/freeze on|off|status` manage freeze mode",
                "- `/plan` toggle plan preference metadata",
                "- `/learn <feedback>` publish a learned policy from last answer",
                "- `/undo` rollback latest learned policy",
                "- `/init` create starter project files",
                "- aliases: `/setup` or `/set-up` -> `/init`",
            ]
        )

    def cmd_help(self, args: list[str]) -> CommandResult:
        return CommandResult("help", self._help_text())

    def cmd_tools(self, args: list[str]) -> CommandResult:
        data = {"tools": self.runtime.tool_registry.list_tools()}
        return CommandResult("tools", dump_yaml(data), data)

    def cmd_skills(self, args: list[str]) -> CommandResult:
        data = {"skills": self.runtime.skill_inventory()}
        return CommandResult("skills", dump_yaml(data), data)

    def cmd_harness(self, args: list[str]) -> CommandResult:
        data = self.runtime.harness_inventory()
        return CommandResult("harness", dump_yaml(data), data)

    def cmd_threads(self, args: list[str]) -> CommandResult:
        data = self.runtime.thread_inventory()
        return CommandResult("threads", dump_yaml(data), data)

    def cmd_memory(self, args: list[str]) -> CommandResult:
        if not args or args[0] == "list":
            data = {"memory": self.runtime.memory_list()}
            return CommandResult("memory", dump_yaml(data), data)

        action = args[0]
        if action == "show":
            if len(args) < 2 or ":" not in args[1]:
                text = "Usage: /memory show <scope>:<name>"
                return CommandResult("memory", text, {"ok": False, "reason": text})
            scope, name = args[1].split(":", 1)
            data = self.runtime.memory_show(scope, name)
            return CommandResult("memory", dump_yaml(data), data)

        if action in {"add", "set", "remove"}:
            if len(args) < 2:
                text = f"Usage: /memory {action} <name>" + (" <text>" if action != "remove" else "")
                return CommandResult("memory", text, {"ok": False, "reason": text})
            raw_name = args[1]
            if ":" in raw_name:
                scope, name = raw_name.split(":", 1)
                if scope != "global_manual":
                    text = f"{scope} memory is read-only; only global_manual can be edited via /memory"
                    return CommandResult("memory", text, {"ok": False, "reason": text})
            else:
                name = raw_name
            if action == "remove":
                data = self.runtime.memory_remove(name)
                return CommandResult("memory", dump_yaml(data), data)
            if len(args) < 3:
                text = f"Usage: /memory {action} <name> <text>"
                return CommandResult("memory", text, {"ok": False, "reason": text})
            text_value = " ".join(args[2:]).strip()
            data = self.runtime.memory_add(name, text_value) if action == "add" else self.runtime.memory_set(name, text_value)
            return CommandResult("memory", dump_yaml(data), data)

        text = "Usage: /memory [list|show|add|set|remove]"
        return CommandResult("memory", text, {"ok": False, "reason": text})

    def cmd_student(self, args: list[str]) -> CommandResult:
        if not args or args[0] in {"status", "show-status"}:
            data = self.runtime.student_status()
            return CommandResult("student", dump_yaml(data), data)
        action = args[0]
        if action == "list":
            kind = args[1] if len(args) > 1 else None
            data = self.runtime.student_inventory(kind)
            return CommandResult("student", dump_yaml(data), data)
        if action == "show":
            if len(args) < 2:
                text = "Usage: /student show <entry_id>"
                return CommandResult("student", text, {"ok": False, "reason": text})
            data = self.runtime.student_show(args[1])
            return CommandResult("student", dump_yaml(data), data)
        if action == "add":
            if len(args) < 4:
                text = "Usage: /student add <kind> <title> <text>"
                return CommandResult("student", text, {"ok": False, "reason": text})
            kind = args[1]
            title = args[2]
            text_value = " ".join(args[3:]).strip()
            data = self.runtime.student_add(kind, title, text_value)
            return CommandResult("student", dump_yaml(data), data)
        text = "Usage: /student [status|list|show|add]"
        return CommandResult("student", text, {"ok": False, "reason": text})

    def cmd_learned(self, args: list[str]) -> CommandResult:
        data = {"learned": self.runtime.learning_manager.list_learned()}
        return CommandResult("learned", dump_yaml(data), data)

    def cmd_policies(self, args: list[str]) -> CommandResult:
        return self.cmd_learned(args)

    def cmd_permissions(self, args: list[str]) -> CommandResult:
        data = self.runtime.permissions.explain()
        return CommandResult("permissions", dump_yaml(data), data)

    def cmd_context(self, args: list[str]) -> CommandResult:
        data = self.runtime.current_context()
        return CommandResult("context", dump_yaml(data), data)

    def cmd_status(self, args: list[str]) -> CommandResult:
        data = self.runtime.status()
        return CommandResult("status", dump_yaml(data), data)

    def cmd_sessions(self, args: list[str]) -> CommandResult:
        data = {"sessions": self.runtime.sessions.list()}
        return CommandResult("sessions", dump_yaml(data), data)

    def cmd_resume(self, args: list[str]) -> CommandResult:
        session_id = args[0] if args else None
        data = self.runtime.resume_session(session_id)
        return CommandResult("resume", dump_yaml(data), data)

    def cmd_new(self, args: list[str]) -> CommandResult:
        title = " ".join(args).strip() or "session"
        data = self.runtime.new_session(title=title)
        return CommandResult("new", dump_yaml(data), data)

    def cmd_config(self, args: list[str]) -> CommandResult:
        data = self.runtime.config_dict()
        return CommandResult("config", dump_yaml(data), data)

    def cmd_doctor(self, args: list[str]) -> CommandResult:
        data = self.runtime.doctor()
        return CommandResult("doctor", dump_yaml(data), data)

    def cmd_why(self, args: list[str]) -> CommandResult:
        data = self.runtime.why()
        return CommandResult("why", dump_yaml(data), data)

    def cmd_trace(self, args: list[str]) -> CommandResult:
        data = self.runtime.last_trace()
        return CommandResult("trace", dump_yaml(data), data)

    def cmd_compact(self, args: list[str]) -> CommandResult:
        data = self.runtime.compact_session()
        return CommandResult("compact", dump_yaml(data), data)

    def cmd_freeze(self, args: list[str]) -> CommandResult:
        if not args:
            data = self.runtime.set_freeze_mode(not self.runtime.freeze_enabled)
            return CommandResult("freeze", dump_yaml(data), data)
        action = args[0].lower()
        if action == "status":
            data = self.runtime.freeze_status()
            return CommandResult("freeze", dump_yaml(data), data)
        if action in {"on", "true", "1"}:
            data = self.runtime.set_freeze_mode(True)
            return CommandResult("freeze", dump_yaml(data), data)
        if action in {"off", "false", "0"}:
            data = self.runtime.set_freeze_mode(False)
            return CommandResult("freeze", dump_yaml(data), data)
        text = "Usage: /freeze [on|off|status]"
        return CommandResult("freeze", text, {"freeze_mode": self.runtime.freeze_enabled, "reason": text})

    def cmd_plan(self, args: list[str]) -> CommandResult:
        enabled = True
        if args and args[0].lower() in {"off", "false", "0", "disable"}:
            enabled = False
        data = self.runtime.set_plan_mode(enabled)
        return CommandResult("plan", dump_yaml(data), data)

    def cmd_undo(self, args: list[str]) -> CommandResult:
        data = self.runtime.undo()
        return CommandResult("undo", dump_yaml(data), data)

    def cmd_init(self, args: list[str]) -> CommandResult:
        data = self.runtime.init_scaffold()
        return CommandResult("init", dump_yaml(data), data)
