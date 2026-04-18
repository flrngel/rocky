"""REPL must absorb KeyboardInterrupt raised from runtime.run_prompt and return
to the rocky> prompt instead of crashing the process with a traceback."""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console

from rocky.ui.repl import RockyRepl


def _make_runtime(tmp_path: Path) -> MagicMock:
    runtime = MagicMock()
    runtime.workspace.cache_dir = tmp_path
    runtime.commands.names = ["help", "exit"]
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
    runtime.current_session_usage.return_value = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "requests": 0,
    }
    return runtime


def test_repl_ctrl_c_during_run_prompt_returns_to_loop(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    runtime.run_prompt.side_effect = KeyboardInterrupt

    repl = RockyRepl(runtime)

    # capture rich console output into a buffer we can assert on
    buffer = io.StringIO()
    repl.console = Console(file=buffer, force_terminal=False, no_color=True, width=80)

    # feed one user line, then EOFError to exit the outer loop cleanly
    lines = iter(["search coffee machines under 1000"])

    def fake_prompt(*args, **kwargs):
        try:
            return next(lines)
        except StopIteration:
            raise EOFError

    repl.session.prompt = fake_prompt  # type: ignore[assignment]

    rc = repl.run()

    assert rc == 0, "REPL must exit cleanly even after a Ctrl+C in run_prompt"
    output = buffer.getvalue()
    assert "interrupted" in output, (
        "expected an 'interrupted' cancellation line; got output:\n" + output
    )
    # Ready-banner + interrupted + bye — ensure the outer loop really continued
    assert "bye" in output
    runtime.run_prompt.assert_called_once()
