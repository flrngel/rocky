from __future__ import annotations

from dataclasses import dataclass, field

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.json import JSON
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from rocky.commands.registry import CommandResult
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


@dataclass(slots=True)
class EventPrinter:
    console: Console
    streamed_text: bool = field(default=False)
    _stream_open: bool = field(default=False)

    def _ensure_stream_line(self) -> None:
        if not self._stream_open:
            self.console.print(Text("assistant ", style="bold bright_white"), end="")
            self._stream_open = True

    def _close_stream_line(self) -> None:
        if self._stream_open:
            self.console.print()
            self._stream_open = False

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "assistant_chunk":
            self.streamed_text = True
            self._ensure_stream_line()
            self.console.print(Text(str(event.get("text", ""))), end="")
        elif kind == "tool_call":
            self._close_stream_line()
            body = Text()
            body.append(str(event.get("name", "")), style="bold cyan")
            body.append("\n")
            body.append(safe_json(event.get("arguments") or {}))
            self.console.print(Panel(body, title="tool call", border_style="cyan"))
        elif kind == "tool_result":
            self._close_stream_line()
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
        self.prompt_style = Style.from_dict(
            {
                "prompt": "bold ansibrightblue",
                "accent": "bold ansicyan",
                "toolbar": "reverse",
                "continuation": "ansibrightblack",
            }
        )
        history_path = runtime.workspace.cache_dir / "repl_history.txt"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        _kb = KeyBindings()

        @_kb.add("enter")
        def _submit(event):
            event.current_buffer.validate_and_handle()

        @_kb.add("escape", "enter")
        def _newline(event):
            event.current_buffer.insert_text("\n")

        self.session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=build_completer(runtime.commands.names),
            multiline=True,
            key_bindings=_kb,
            style=self.prompt_style,
            complete_while_typing=False,
            reserve_space_for_menu=6,
        )

    def _prompt_message(self) -> HTML:
        return HTML("<prompt>rocky</prompt><accent>&gt;</accent> ")

    def _toolbar(self) -> HTML:
        return HTML("<toolbar> Enter submit  Alt+Enter newline  /help commands </toolbar>")

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
            if line.startswith("/"):
                result = self.runtime.commands.handle(line)
                self.print_command_result(result)
                continue
            printer = EventPrinter(self.console)
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
