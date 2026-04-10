from __future__ import annotations

from dataclasses import dataclass, field

from prompt_toolkit import PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.json import JSON
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from rocky.commands.registry import CommandResult
from rocky.config.loader import ConfigLoader
from rocky.tool_events import tool_event_debug_text, tool_event_summary_text
from rocky.config.wizard import run_config_wizard
from rocky.ui.completion import build_completer
from rocky.util.text import safe_json


def render_console_text(console: Console, text: str) -> None:
    stripped = text.strip()
    if not stripped:
        return
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            console.print(JSON(stripped))
            return
        except Exception:
            pass
    if stripped.startswith("#") or stripped.startswith("- ") or stripped.startswith("```"):
        console.print(Markdown(text))
        return
    console.print(Text(text))


def make_live_console(console: Console) -> Console:
    return Console(
        file=console.file,
        stderr=console.stderr,
        no_color=True,
        highlight=False,
        force_terminal=False,
    )


@dataclass(slots=True)
class EventPrinter:
    console: Console
    response_marker: str = field(default="| ")
    verbose: bool = field(default=False)
    streamed_text: bool = field(default=False)
    _stream_open: bool = field(default=False)
    _last_status_text: str = field(default="")

    def _ensure_stream_line(self) -> None:
        if not self._stream_open:
            self.console.print(Text(self.response_marker, style="bold bright_black"), end="")
            self._stream_open = True

    def _close_stream_line(self) -> None:
        if self._stream_open:
            self.console.print()
            self._stream_open = False

    def _tool_summary(self, event: dict) -> str:
        summary = tool_event_summary_text(event).strip()
        if summary:
            return summary
        text = str(event.get("text", "")).strip()
        if not text:
            return "done"
        return text.splitlines()[0][:160]

    def _tool_call_message(self, name: str) -> str:
        messages = {
            "search_web": "Searching the web...",
            "fetch_url": "Opening the source...",
            "agent_browser": "Browsing the page...",
            "extract_links": "Scanning page links...",
            "browser_render_page": "Opening the page...",
            "browser_screenshot": "Capturing the page...",
            "run_shell_command": "Running a command...",
            "inspect_runtime_versions": "Checking installed runtimes...",
            "inspect_shell_environment": "Checking the local shell...",
            "read_shell_history": "Checking recent shell history...",
            "list_files": "Scanning the workspace...",
            "glob_paths": "Scanning the workspace...",
            "grep_files": "Searching project files...",
            "read_file": "Reading files...",
            "write_file": "Updating files...",
            "replace_in_file": "Updating files...",
            "move_path": "Organizing files...",
            "copy_path": "Copying files...",
            "delete_path": "Removing files...",
            "run_python": "Analyzing with Python...",
            "inspect_spreadsheet": "Inspecting the spreadsheet...",
            "read_sheet_range": "Reading spreadsheet rows...",
            "git_status": "Checking git status...",
            "git_diff": "Checking git changes...",
            "git_recent_commits": "Checking recent commits...",
        }
        return messages.get(name, "Working...")

    def _tool_failure_message(self, event: dict) -> str:
        name = str(event.get("name", "") or "")
        summary = self._tool_summary(event)
        messages = {
            "search_web": "Couldn't search the web.",
            "fetch_url": "Couldn't open that source.",
            "agent_browser": "Couldn't browse that page.",
            "extract_links": "Couldn't scan links from that page.",
            "browser_render_page": "Couldn't open that page.",
            "browser_screenshot": "Couldn't capture that page.",
            "run_shell_command": "Couldn't run that command.",
            "inspect_runtime_versions": "Couldn't inspect installed runtimes.",
            "inspect_shell_environment": "Couldn't inspect the local shell.",
            "read_shell_history": "Couldn't read recent shell history.",
            "list_files": "Couldn't inspect the workspace.",
            "glob_paths": "Couldn't inspect the workspace.",
            "grep_files": "Couldn't search the project files.",
            "read_file": "Couldn't read that file.",
            "write_file": "Couldn't update the file.",
            "replace_in_file": "Couldn't update the file.",
            "move_path": "Couldn't move that file.",
            "copy_path": "Couldn't copy that file.",
            "delete_path": "Couldn't delete that file.",
            "run_python": "Couldn't analyze the result.",
            "inspect_spreadsheet": "Couldn't inspect the spreadsheet.",
            "read_sheet_range": "Couldn't read the spreadsheet rows.",
            "git_status": "Couldn't inspect git status.",
            "git_diff": "Couldn't inspect git changes.",
            "git_recent_commits": "Couldn't inspect recent commits.",
        }
        base = messages.get(name, "A step failed.")
        if not summary or summary == "done":
            return base
        return f"{base} {summary}"

    def _print_status(self, text: str, *, style: str) -> None:
        if text == self._last_status_text:
            return
        self.console.print(Text(text, style=style))
        self._last_status_text = text

    def _self_learning_message(self, event: dict) -> str:
        persisted = bool(event.get("persisted"))
        summary = str(event.get("summary", "")).strip()
        title = str(event.get("title", "")).strip()
        reason = str(event.get("reason", "")).strip()
        if persisted:
            detail = summary or title or "stored a compact retrospective."
            return f"Learned: {detail}"
        if reason:
            return f"Reflection kept no durable lesson: {reason}"
        return "Reflection kept no durable lesson."

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "assistant_chunk":
            self.streamed_text = True
            self._ensure_stream_line()
            self.console.print(Text(str(event.get("text", ""))), end="")
        elif kind == "self_learning_start":
            self._close_stream_line()
            if self.verbose:
                self.console.print(Text("Reflecting on this turn...", style="dim"))
            return
        elif kind == "self_learning_result":
            self._close_stream_line()
            if event.get("persisted"):
                self.console.print(Text(self._self_learning_message(event), style="green"))
            elif self.verbose:
                self.console.print(Text(self._self_learning_message(event), style="dim"))
            return
        elif kind == "tool_call":
            self._close_stream_line()
            if not self.verbose:
                self._print_status(self._tool_call_message(str(event.get("name", ""))), style="cyan")
                return
            body = Text()
            body.append(str(event.get("name", "")), style="bold cyan")
            body.append("\n")
            body.append(safe_json(event.get("arguments") or {}))
            self.console.print(Panel(body, title="tool call", border_style="cyan"))
        elif kind == "tool_result":
            self._close_stream_line()
            if not self.verbose:
                if event.get("success"):
                    return
                self._print_status(self._tool_failure_message(event), style="red")
                return
            self.console.print(
                Panel(
                    Text(tool_event_debug_text(event)),
                    title=f"tool result: {event.get('name', '')}",
                    border_style="green" if event.get("success") else "red",
                )
            )

    def finish(self) -> None:
        self._close_stream_line()


class RockyRepl:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.console = Console()
        self.live_console = make_live_console(self.console)
        self.prompt_style = Style.from_dict(
            {
                "prompt": "bold ansibrightblue",
                "accent": "bold ansicyan",
                "toolbar": "reverse",
                "continuation": "ansibrightblack",
            }
        )
        if runtime.freeze_enabled:
            history = InMemoryHistory()
        else:
            history_path = runtime.workspace.cache_dir / "repl_history.txt"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(history_path))
        _kb = KeyBindings()

        @_kb.add("enter")
        def _submit(event):
            event.current_buffer.validate_and_handle()

        @_kb.add("escape", "enter")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @_kb.add("c-r")
        def _resume(event):
            self._dispatch_shortcut(event, "/resume")

        @_kb.add("c-n")
        def _new(event):
            self._dispatch_shortcut(event, "/new ")

        @_kb.add("c-t")
        def _status(event):
            self._dispatch_shortcut(event, "/status")

        @_kb.add("c-f")
        def _freeze(event):
            self._dispatch_shortcut(event, "/freeze")

        @_kb.add("c-g")
        def _student(event):
            self._dispatch_shortcut(event, "/student")

        self.session = PromptSession(
            history=history,
            completer=build_completer(runtime),
            multiline=True,
            key_bindings=_kb,
            style=self.prompt_style,
            complete_while_typing=True,
            reserve_space_for_menu=8,
        )

    def _dispatch_shortcut(self, event, text: str) -> None:
        buffer = event.current_buffer
        buffer.document = Document(text, cursor_position=len(text))
        buffer.validate_and_handle()

    def _prompt_message(self) -> HTML:
        if self.runtime.freeze_enabled:
            return HTML("<prompt>rocky</prompt><accent>[freeze]&gt;</accent> ")
        return HTML("<prompt>rocky</prompt><accent>&gt;</accent> ")

    def _safe_session_id(self) -> str:
        method = getattr(type(self.runtime), "_status_session", None)
        if method is None:
            return ""
        try:
            current = self.runtime._status_session()
            session_id = getattr(current, "id", "") if current is not None else ""
            value = str(session_id or "")
            return "" if "MagicMock" in value else value
        except Exception:
            return ""

    def _safe_provider_label(self) -> str:
        config = getattr(self.runtime, "config", None)
        if config is None:
            return ""
        provider_name = str(getattr(config, "active_provider", "") or "")
        if not provider_name or "MagicMock" in provider_name or "<" in provider_name or ">" in provider_name:
            return ""
        provider_method = getattr(type(config), "provider", None)
        if provider_method is None:
            return provider_name
        try:
            provider = config.provider(provider_name)
            model = str(getattr(provider, "model", "") or "")
            if "MagicMock" in model or "<" in model or ">" in model:
                return provider_name
            return f"{provider_name}:{model}" if provider_name or model else ""
        except Exception:
            return provider_name

    def _safe_thread_id(self) -> str:
        method = getattr(type(self.runtime), "thread_inventory", None)
        if method is None:
            return ""
        try:
            payload = self.runtime.thread_inventory()
            thread_id = str((payload or {}).get("current_thread_id") or "")
            return "" if "MagicMock" in thread_id else thread_id
        except Exception:
            return ""

    def _default_context_usage(self) -> dict[str, int]:
        return {
            "instructions": 0,
            "memories": 0,
            "skills": 0,
            "learned_policies": 0,
            "student_notes": 0,
            "handoffs": 0,
        }

    def _default_session_usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
        }

    def _summarize_context_usage(self, context: object) -> dict[str, int]:
        if not isinstance(context, dict):
            return self._default_context_usage()

        def _count(name: str) -> int:
            value = context.get(name) or []
            return len(value) if isinstance(value, list) else 0

        return {
            "instructions": _count("instructions"),
            "memories": _count("memories"),
            "skills": _count("skills"),
            "learned_policies": _count("learned_policies"),
            "student_notes": _count("student_notes"),
            "handoffs": _count("handoffs"),
        }

    def _normalize_context_usage(self, payload: object) -> dict[str, int]:
        if not isinstance(payload, dict):
            return self._default_context_usage()
        expected = self._default_context_usage().keys()
        if all(isinstance(payload.get(name), int) for name in expected):
            return {name: int(payload.get(name) or 0) for name in expected}
        return self._summarize_context_usage(payload)

    def _safe_context_usage(self) -> dict[str, int]:
        method = getattr(type(self.runtime), "context_usage", None)
        if method is not None:
            try:
                payload = method(self.runtime)
                if isinstance(payload, dict):
                    return self._normalize_context_usage(payload)
            except Exception:
                pass
        fallback = getattr(self.runtime, "current_context", None)
        if callable(fallback):
            try:
                return self._normalize_context_usage(fallback())
            except Exception:
                pass
        return self._default_context_usage()

    def _normalize_session_usage(self, payload: object) -> dict[str, int]:
        if not isinstance(payload, dict):
            return self._default_session_usage()
        normalized = {}
        for key in self._default_session_usage():
            try:
                normalized[key] = max(0, int(payload.get(key) or 0))
            except Exception:
                normalized[key] = 0
        if normalized["total_tokens"] <= 0:
            normalized["total_tokens"] = normalized["prompt_tokens"] + normalized["completion_tokens"]
        return normalized

    def _safe_session_usage(self) -> dict[str, int]:
        method = getattr(type(self.runtime), "current_session_usage", None)
        if method is not None:
            try:
                payload = method(self.runtime)
                if isinstance(payload, dict):
                    return self._normalize_session_usage(payload)
            except Exception:
                pass
        fallback = getattr(self.runtime, "current_session_usage", None)
        if callable(fallback):
            try:
                return self._normalize_session_usage(fallback())
            except Exception:
                pass
        return self._default_session_usage()

    def _context_usage_label(self) -> str:
        usage = self._safe_context_usage()
        return (
            f"Ctx I{usage['instructions']}"
            f" M{usage['memories']}"
            f" S{usage['skills']}"
            f" P{usage['learned_policies']}"
            f" N{usage['student_notes']}"
            f" H{usage['handoffs']}"
        )

    def _session_usage_label(self) -> str:
        usage = self._safe_session_usage()
        return (
            f"Tok P{usage['prompt_tokens']}"
            f" C{usage['completion_tokens']}"
            f" T{usage['total_tokens']}"
        )

    def _toolbar(self) -> HTML:
        freeze_label = "Freeze: ON" if self.runtime.freeze_enabled else "Freeze: OFF"
        verbose_label = "Verbose: ON" if getattr(self.runtime, "verbose_enabled", False) else "Verbose: OFF"
        parts = [
            "Enter submit",
            "Alt+Enter newline",
            "Ctrl-R resume",
            "Ctrl-N new",
            "Ctrl-T status",
            "Ctrl-F freeze",
            "Ctrl-G student",
            freeze_label,
            verbose_label,
            self._session_usage_label(),
            self._context_usage_label(),
        ]
        session_id = self._safe_session_id()
        if session_id:
            parts.append(f"Session: {session_id[-8:]}")
        provider_label = self._safe_provider_label()
        if provider_label:
            parts.append(provider_label)
        thread_id = self._safe_thread_id()
        if thread_id:
            parts.append(f"Thread: {thread_id[-8:]}")
        return HTML("<toolbar> " + "  ".join(parts) + " </toolbar>")

    def _continuation(self, width: int, line_number: int, wrap_count: int):
        return [("class:continuation", "... ".rjust(width))]

    def print_text(self, text: str) -> None:
        render_console_text(self.console, text)

    def print_command_result(self, result: CommandResult) -> None:
        self.print_text(result.text)

    def run_config_wizard(self) -> None:
        if self.runtime.freeze_enabled:
            self.console.print(
                "Freeze mode is enabled; /configure is disabled because it would write persistent config.",
                markup=False,
                style="yellow",
            )
            return
        loader = ConfigLoader(self.runtime.global_root, self.runtime.workspace.root)
        run_config_wizard(loader.global_config, console=self.console)
        self.runtime.reload_config()
        self.console.print("[bold green]Runtime config reloaded.[/]")

    def run(self) -> int:
        self.console.print("[bold green]Rocky[/] ready. Type /help for controls.")
        while True:
            try:
                line = self.session.prompt(
                    self._prompt_message(),
                    bottom_toolbar=self._toolbar,
                    prompt_continuation=self._continuation,
                ).strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print("\n[dim]bye[/]")
                return 0
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                self.console.print("[dim]bye[/]")
                return 0
            if line == "/configure":
                self.run_config_wizard()
                continue
            if line.startswith("/"):
                result = self.runtime.commands.handle(line)
                self.print_command_result(result)
                continue
            printer = EventPrinter(self.live_console, verbose=getattr(self.runtime, "verbose_enabled", False))
            with patch_stdout():
                response = self.runtime.run_prompt(line, stream=True, event_handler=printer)
            if printer.streamed_text:
                printer.finish()
            else:
                self.print_text(response.text)
            if response.verification.get("status") != "pass":
                self.console.print(
                    Panel(
                        Text(str(response.verification.get("message", ""))),
                        title="verification",
                        border_style="yellow",
                    )
                )
