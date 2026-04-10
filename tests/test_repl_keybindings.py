"""Tests for REPL key bindings: Enter submits, Alt+Enter adds newline, session wiring intact."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from prompt_toolkit import PromptSession
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.keys import Keys

from rocky.ui.repl import RockyRepl


def _make_runtime(tmp_path: Path) -> MagicMock:
    """Build a minimal mock runtime for RockyRepl construction."""
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


def _key_tuples(kb):
    """Extract (key-tuple, handler) pairs from a KeyBindings object."""
    return [(b.keys, b.handler) for b in kb.bindings]


# --- P0-1: Enter key submits input ---

def test_enter_submits(tmp_path):
    repl = RockyRepl(_make_runtime(tmp_path))
    kb = repl.session.key_bindings

    # Find the Enter binding
    enter_bindings = [b for b in kb.bindings if b.keys == (Keys.Enter,)]
    assert enter_bindings, "No Enter key binding found in session key_bindings"

    # Verify handler calls validate_and_handle (submit), not insert_text
    handler = enter_bindings[0].handler
    mock_event = MagicMock()
    handler(mock_event)
    mock_event.current_buffer.validate_and_handle.assert_called_once()
    mock_event.current_buffer.insert_text.assert_not_called()

    # Anti-monkey: multiline must still be True (Option B, not Option A)
    assert repl.session.default_buffer.multiline() is True


# --- P0-2: Alt+Enter adds newline (multiline preserved) ---

def test_alt_enter_newline(tmp_path):
    repl = RockyRepl(_make_runtime(tmp_path))
    kb = repl.session.key_bindings

    # Find the Escape+Enter binding (Alt+Enter in prompt_toolkit)
    escape_enter_bindings = [
        b for b in kb.bindings if b.keys == (Keys.Escape, Keys.Enter)
    ]
    assert escape_enter_bindings, "No Escape+Enter (Alt+Enter) key binding found"

    # Verify handler inserts a newline
    handler = escape_enter_bindings[0].handler
    mock_event = MagicMock()
    handler(mock_event)
    mock_event.current_buffer.insert_text.assert_called_once_with("\n")
    mock_event.current_buffer.validate_and_handle.assert_not_called()

    # multiline must still be True
    assert repl.session.default_buffer.multiline() is True


# --- P1-3: Session wiring ---

def test_session_wiring(tmp_path):
    repl = RockyRepl(_make_runtime(tmp_path))

    # Session is a PromptSession
    assert isinstance(repl.session, PromptSession)

    # Completer is wired
    assert repl.session.completer is not None

    # History is FileHistory
    assert isinstance(repl.session.history, FileHistory)


def test_freeze_repl_uses_in_memory_history_and_toolbar(tmp_path):
    runtime = _make_runtime(tmp_path)
    runtime.freeze_enabled = True

    repl = RockyRepl(runtime)

    assert isinstance(repl.session.history, InMemoryHistory)
    assert "Freeze: ON" in repl._toolbar().value
    assert "Verbose: OFF" in repl._toolbar().value
    assert "Tok P0 C0 T0" in repl._toolbar().value
    assert "Ctx I0 M0 S0 P0 N0 H0" in repl._toolbar().value
    assert "freeze" in repl._prompt_message().value


def test_non_freeze_repl_uses_file_history(tmp_path):
    repl = RockyRepl(_make_runtime(tmp_path))

    assert isinstance(repl.session.history, FileHistory)


def test_verbose_repl_toolbar_shows_enabled(tmp_path):
    runtime = _make_runtime(tmp_path)
    runtime.verbose_enabled = True

    repl = RockyRepl(runtime)

    assert "Verbose: ON" in repl._toolbar().value


def test_repl_toolbar_shows_context_usage_counts(tmp_path):
    runtime = _make_runtime(tmp_path)
    runtime.current_context.return_value = {
        "instructions": [{"path": "/tmp/AGENTS.md"}],
        "memories": [{"id": "mem1"}, {"id": "mem2"}],
        "skills": [{"name": "general-operator"}],
        "learned_policies": [{"name": "runtime-check"}],
        "student_notes": [{"id": "note1"}, {"id": "note2"}, {"id": "note3"}],
        "handoffs": [{"session_id": "ses1"}],
    }

    repl = RockyRepl(runtime)

    assert "Ctx I1 M2 S1 P1 N3 H1" in repl._toolbar().value


def test_repl_toolbar_shows_session_token_usage(tmp_path):
    runtime = _make_runtime(tmp_path)
    runtime.current_session_usage.return_value = {
        "prompt_tokens": 120,
        "completion_tokens": 45,
        "total_tokens": 165,
        "requests": 3,
    }

    repl = RockyRepl(runtime)

    assert "Tok P120 C45 T165" in repl._toolbar().value


def test_slash_command_completion_matches_prefix(tmp_path):
    repl = RockyRepl(_make_runtime(tmp_path))

    completions = list(repl.session.completer.get_completions(Document("/he"), None))

    assert any(item.text == "/help" for item in completions)


def test_non_command_text_does_not_offer_slash_completion(tmp_path):
    repl = RockyRepl(_make_runtime(tmp_path))

    completions = list(repl.session.completer.get_completions(Document("hello"), None))

    assert completions == []
