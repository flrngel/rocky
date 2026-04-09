from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from rich.panel import Panel

from rocky.ui.repl import EventPrinter, RockyRepl


def _make_runtime(tmp_path: Path) -> MagicMock:
    runtime = MagicMock()
    runtime.workspace.cache_dir = tmp_path
    runtime.commands.names = ["help"]
    runtime.freeze_enabled = False
    runtime.verbose_enabled = False
    runtime.current_context.return_value = {
        "instructions": [],
        "memories": [],
        "skills": [],
        "learned_policies": [],
        "student_notes": [],
        "handoffs": [],
    }
    return runtime


def test_plain_text_output_does_not_use_markup(tmp_path: Path) -> None:
    repl = RockyRepl(_make_runtime(tmp_path))
    repl.console = MagicMock()

    repl.print_text("Provider request failed: [Errno 61] Connection refused")

    repl.console.print.assert_called_once()
    renderable = repl.console.print.call_args.args[0]
    assert renderable.plain == "Provider request failed: [Errno 61] Connection refused"


def test_streamed_chunks_preserve_bracket_text(tmp_path: Path) -> None:
    printer = EventPrinter(console=MagicMock())

    printer({"type": "assistant_chunk", "text": "Provider request failed: [Errno 61] Connection refused"})

    first_renderable = printer.console.print.call_args_list[0].args[0]
    second_renderable = printer.console.print.call_args_list[1].args[0]
    assert first_renderable.plain == "| "
    assert second_renderable.plain == "Provider request failed: [Errno 61] Connection refused"


def test_repl_uses_plain_live_console_for_streaming(tmp_path: Path) -> None:
    repl = RockyRepl(_make_runtime(tmp_path))

    assert repl.live_console is not repl.console
    assert repl.live_console.no_color is True


def test_short_tool_logs_are_compact_by_default() -> None:
    printer = EventPrinter(console=MagicMock())

    printer({"type": "tool_call", "name": "run_shell_command", "arguments": {"command": "pwd"}})
    printer(
        {
            "type": "tool_result",
            "name": "run_shell_command",
            "success": True,
            "text": '{"success": true, "summary": "Command exited with 0", "data": {}}',
        }
    )

    assert len(printer.console.print.call_args_list) == 1
    first = printer.console.print.call_args_list[0].args[0]
    assert first.plain == "Running a command..."


def test_default_tool_logs_show_refined_failure_message() -> None:
    printer = EventPrinter(console=MagicMock())

    printer({"type": "tool_call", "name": "fetch_url", "arguments": {"url": "https://example.com"}})
    printer(
        {
            "type": "tool_result",
            "name": "fetch_url",
            "success": False,
            "text": '{"success": false, "summary": "HTTP 403 while fetching https://example.com", "data": {}}',
        }
    )

    first = printer.console.print.call_args_list[0].args[0]
    second = printer.console.print.call_args_list[1].args[0]
    assert first.plain == "Opening the source..."
    assert second.plain == "Couldn't open that source. HTTP 403 while fetching https://example.com"


def test_self_learning_result_is_visible_when_a_lesson_is_persisted() -> None:
    printer = EventPrinter(console=MagicMock())

    printer(
        {
            "type": "self_learning_result",
            "persisted": True,
            "summary": "Use inspect_runtime_versions before shell confirmation for runtime questions.",
        }
    )

    first = printer.console.print.call_args_list[0].args[0]
    assert first.plain == "Learned: Use inspect_runtime_versions before shell confirmation for runtime questions."


def test_verbose_mode_shows_self_learning_process_even_without_persisted_lesson() -> None:
    printer = EventPrinter(console=MagicMock(), verbose=True)

    printer({"type": "self_learning_start"})
    printer({"type": "self_learning_result", "persisted": False, "reason": "reflection found no durable lesson to keep"})

    first = printer.console.print.call_args_list[0].args[0]
    second = printer.console.print.call_args_list[1].args[0]
    assert first.plain == "Reflecting on this turn..."
    assert second.plain == "Reflection kept no durable lesson: reflection found no durable lesson to keep"


def test_verbose_tool_logs_use_panels() -> None:
    printer = EventPrinter(console=MagicMock(), verbose=True)

    printer({"type": "tool_call", "name": "run_shell_command", "arguments": {"command": "pwd"}})
    printer(
        {
            "type": "tool_result",
            "name": "run_shell_command",
            "success": True,
            "text": '{"success": true, "summary": "Command exited with 0", "data": {}}',
        }
    )

    assert isinstance(printer.console.print.call_args_list[0].args[0], Panel)
    assert isinstance(printer.console.print.call_args_list[1].args[0], Panel)
