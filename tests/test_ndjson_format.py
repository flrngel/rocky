Status: DONE  # xlfg artifact status — valid Python module-level annotation
"""Tests for O9 — stable ndjson streaming format (--format ndjson)."""

import io
import json
import subprocess
from pathlib import Path

import pytest

from rocky.cli import build_parser
from rocky.ui.ndjson_printer import NdjsonEventPrinter


# ---------------------------------------------------------------------------
# 1. Unit — NdjsonEventPrinter.handle / __call__
# ---------------------------------------------------------------------------

class TestNdjsonEventPrinterUnit:
    def test_dict_event_round_trips(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)
        event = {"type": "tool_call", "name": "search_web", "args": {"q": "x"}}
        printer.handle(event)
        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["type"] == "tool_call"
        assert parsed["name"] == "search_web"

    def test_multiple_events_each_parseable(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)
        events = [
            {"type": "tool_call", "name": "read_file"},
            {"type": "tool_result", "name": "read_file", "success": True},
            {"type": "answer", "text": "done"},
        ]
        for e in events:
            printer(e)
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "type" in parsed

    def test_non_dict_event_no_crash(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)
        printer("just a string")
        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert "type" in parsed or "value" in parsed

    def test_unicode_preserved(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)
        printer({"type": "answer", "text": "こんにちは"})
        line = buf.getvalue().strip()
        parsed = json.loads(line)
        assert parsed["text"] == "こんにちは"

    def test_streamed_text_flag_set_on_assistant_chunk(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)
        assert printer.streamed_text is False
        printer({"type": "assistant_chunk", "text": "hello"})
        assert printer.streamed_text is True

    def test_finish_is_noop(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)
        printer.finish()  # must not raise
        assert buf.getvalue() == ""

    def test_non_serializable_object_handled(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)

        class Unserializable:
            def __str__(self):
                return "unserializable_obj"

        printer(Unserializable())
        line = buf.getvalue().strip()
        parsed = json.loads(line)  # must be valid JSON — no crash is the key assertion
        assert isinstance(parsed, dict)

    def test_each_event_is_one_line(self):
        buf = io.StringIO()
        printer = NdjsonEventPrinter(stream=buf)
        for i in range(5):
            printer({"type": "tool_call", "i": i})
        lines = buf.getvalue().splitlines()
        assert len(lines) == 5
        for line in lines:
            json.loads(line)  # each line is valid JSON


# ---------------------------------------------------------------------------
# 2. CLI-parser wiring
# ---------------------------------------------------------------------------

class TestCliParserWiring:
    def test_format_ndjson_parsed(self):
        args = build_parser().parse_args(["--format", "ndjson", "task"])
        assert args.format == "ndjson"

    def test_format_defaults_to_none(self):
        args = build_parser().parse_args(["task"])
        assert args.format is None

    def test_format_bogus_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            build_parser().parse_args(["--format", "bogus", "task"])
        assert exc_info.value.code != 0

    def test_verbose_path_unchanged(self):
        args = build_parser().parse_args(["--verbose", "task"])
        assert args.verbose is True
        assert args.format is None

    def test_verbose_and_format_both_parsed(self):
        # argparse itself does NOT block this combo — cli.main validates it at runtime.
        args = build_parser().parse_args(["--verbose", "--format", "ndjson", "task"])
        assert args.verbose is True
        assert args.format == "ndjson"

    def test_format_choices_enforced(self):
        """Only the listed choices should be accepted."""
        for bad in ["plain", "jsonl", "xml"]:
            with pytest.raises(SystemExit) as exc_info:
                build_parser().parse_args(["--format", bad, "task"])
            assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# 3. CLI subprocess smoke test
# ---------------------------------------------------------------------------

_VENV_ROCKY = Path(__file__).parent.parent / ".venv" / "bin" / "rocky"


def _ollama_available() -> bool:
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "2", "http://localhost:11434/"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(
    not _VENV_ROCKY.exists(),
    reason="rocky binary not found in .venv — install first",
)
@pytest.mark.skipif(
    not _ollama_available(),
    reason="Ollama not running — skip live subprocess test",
)
def test_format_ndjson_subprocess(tmp_path):
    result = subprocess.run(
        [str(_VENV_ROCKY), "--format", "ndjson", "--freeze", "--cwd", str(tmp_path), "count words in hello world"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"rocky exited non-zero: {result.stderr}"
    non_empty_lines = [l for l in result.stdout.splitlines() if l.strip()]
    # Every non-empty stdout line must be valid JSON
    for line in non_empty_lines:
        parsed = json.loads(line)
        assert isinstance(parsed, dict), f"Expected dict, got {type(parsed)}: {line}"
    # At least one line should have a "type" field (if any output was produced)
    if non_empty_lines:
        types = [json.loads(l).get("type") for l in non_empty_lines]
        assert any(t is not None for t in types), f"No line had a 'type' field; lines: {non_empty_lines}"


# ---------------------------------------------------------------------------
# 4. Mutual exclusion: --verbose + --format ndjson triggers an error in main
# ---------------------------------------------------------------------------

class TestMutualExclusion:
    def test_verbose_and_ndjson_exits_in_main(self, tmp_path):
        """cli.main must exit(!=0) when both --verbose and --format ndjson are given."""
        if not _VENV_ROCKY.exists():
            pytest.skip("rocky binary not in .venv")
        result = subprocess.run(
            [str(_VENV_ROCKY), "--verbose", "--format", "ndjson", "--freeze", "--cwd", str(tmp_path), "hello"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "mutually exclusive" in combined or "error" in combined.lower()
