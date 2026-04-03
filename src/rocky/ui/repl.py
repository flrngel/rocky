from __future__ import annotations

from dataclasses import dataclass, field
import json

from prompt_toolkit import PromptSession
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
from rocky.config.wizard import run_config_wizard
from rocky.core.permissions import PermissionRequest
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
    speaker_label: str = field(default="Rocky")
    verbose: bool = field(default=False)
    streamed_text: bool = field(default=False)
    _stream_open: bool = field(default=False)

    def _ensure_stream_line(self) -> None:
        if not self._stream_open:
            self.console.print(Text(f"{self.speaker_label} ", style="bold bright_white"), end="")
            self._stream_open = True

    def _close_stream_line(self) -> None:
        if self._stream_open:
            self.console.print()
            self._stream_open = False

    def _tool_summary(self, event: dict) -> str:
        text = str(event.get("text", "")).strip()
        if not text:
            return "done"
        try:
            payload = json.loads(text)
        except Exception:
            return text.splitlines()[0][:160]
        if isinstance(payload, dict):
            summary = str(payload.get("summary", "")).strip()
            if summary:
                return summary
        return text.splitlines()[0][:160]

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "assistant_chunk":
            self.streamed_text = True
            self._ensure_stream_line()
            self.console.print(Text(str(event.get("text", ""))), end="")
        elif kind == "tool_call":
            self._close_stream_line()
            if not self.verbose:
                self.console.print(Text(f"tool: {event.get('name', '')}", style="cyan"))
                return
            body = Text()
            body.append(str(event.get("name", "")), style="bold cyan")
            body.append("\n")
            body.append(safe_json(event.get("arguments") or {}))
            self.console.print(Panel(body, title="tool call", border_style="cyan"))
        elif kind == "tool_result":
            self._close_stream_line()
            if not self.verbose:
                status = "ok" if event.get("success") else "fail"
                summary = self._tool_summary(event)
                style = "green" if event.get("success") else "red"
                self.console.print(Text(f"{status}: {event.get('name', '')} - {summary}", style=style))
                return
            self.console.print(
                Panel(
                    Text(str(event.get("text", ""))),
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

        self.session = PromptSession(
            history=history,
            completer=build_completer(runtime.commands.names),
            multiline=True,
            key_bindings=_kb,
            style=self.prompt_style,
            complete_while_typing=True,
            reserve_space_for_menu=6,
        )

    def _prompt_message(self) -> HTML:
        if self.runtime.freeze_enabled:
            return HTML("<prompt>rocky</prompt><accent>[freeze]&gt;</accent> ")
        return HTML("<prompt>rocky</prompt><accent>&gt;</accent> ")

    def _toolbar(self) -> HTML:
        freeze_label = "Freeze: ON" if self.runtime.freeze_enabled else "Freeze: OFF"
        verbose_label = "Verbose: ON" if getattr(self.runtime, "verbose_enabled", False) else "Verbose: OFF"
        return HTML(
            f"<toolbar> Enter submit  Alt+Enter newline  /help commands  {freeze_label}  {verbose_label} </toolbar>"
        )

    def _continuation(self, width: int, line_number: int, wrap_count: int):
        return [("class:continuation", "... ".rjust(width))]

    def ask_permission(self, request: PermissionRequest) -> bool:
        label = f"Allow {request.family}:{request.action}?"
        if request.detail:
            label += f"\n{request.detail}"
        answer = self.session.prompt(f"{label} [y/N] ", multiline=False)
        return answer.strip().lower() in {"y", "yes"}

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
        if self.runtime.permissions.ask_callback is None:
            self.runtime.permissions.ask_callback = self.ask_permission
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
