from __future__ import annotations

from dataclasses import dataclass, field

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.json import JSON
from rich.markdown import Markdown
from rich.panel import Panel

from rocky.commands.registry import CommandResult
from rocky.core.permissions import PermissionRequest
from rocky.ui.completion import build_completer


@dataclass(slots=True)
class EventPrinter:
    console: Console
    streamed_text: bool = field(default=False)

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "assistant_chunk":
            self.streamed_text = True
            self.console.print(event.get("text", ""), end="")
        elif kind == "tool_call":
            body = f"[bold cyan]{event.get('name', '')}[/]\n{event.get('arguments')}"
            self.console.print(Panel(body, title="tool call", border_style="cyan"))
        elif kind == "tool_result":
            self.console.print(
                Panel(
                    event.get("text", ""),
                    title=f"tool result: {event.get('name', '')}",
                    border_style="green" if event.get("success") else "red",
                )
            )


class RockyRepl:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.console = Console()
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
        )

    def ask_permission(self, request: PermissionRequest) -> bool:
        label = f"Allow {request.family}:{request.action}?"
        if request.detail:
            label += f"\n{request.detail}"
        answer = self.session.prompt(f"{label} [y/N] ", multiline=False)
        return answer.strip().lower() in {"y", "yes"}

    def print_text(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                self.console.print(JSON(stripped))
                return
            except Exception:
                pass
        if stripped.startswith("#") or stripped.startswith("- ") or stripped.startswith("```"):
            self.console.print(Markdown(text))
            return
        self.console.print(text)

    def print_command_result(self, result: CommandResult) -> None:
        self.print_text(result.text)

    def run(self) -> int:
        self.console.print("[bold green]Rocky[/] ready. Type /help for controls.")
        if self.runtime.permissions.ask_callback is None:
            self.runtime.permissions.ask_callback = self.ask_permission
        while True:
            try:
                line = self.session.prompt("[bold blue]rocky> [/]").strip()
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
                self.console.print()
            else:
                self.print_text(response.text)
            if response.verification.get("status") != "pass":
                self.console.print(
                    Panel(
                        response.verification.get("message", ""),
                        title="verification",
                        border_style="yellow",
                    )
                )
