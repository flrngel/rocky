from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from rocky.ui.repl import EventPrinter, RockyRepl


def _make_runtime(tmp_path: Path) -> MagicMock:
    runtime = MagicMock()
    runtime.workspace.cache_dir = tmp_path
    runtime.commands.names = ["help"]
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
    assert first_renderable.plain == "assistant "
    assert second_renderable.plain == "Provider request failed: [Errno 61] Connection refused"
