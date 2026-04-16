"""
O6 — NDJSON streaming extras.

Every emitted event must carry:
- ``seq`` (monotonic, starts at 1, resets per printer instance)
- ``ts`` (ISO-8601 UTC)
- ``schema_version`` (envelope-level contract version)

``--format jsonl`` must be accepted as an alias for ``--format ndjson`` and
canonicalize to ``ndjson`` internally. CF-4: without either format flag, no
NDJSON is emitted and existing stdout behavior is unchanged.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime

import pytest

from rocky.cli import build_parser as build_arg_parser
from rocky.ui.ndjson_printer import NDJSON_SCHEMA_VERSION, NdjsonEventPrinter


# --------------------------------------------------------------------------
# 1. seq is monotonic from 1, ts is ISO-8601, schema_version is present.
# --------------------------------------------------------------------------


def _drain(buf: io.StringIO) -> list[dict]:
    buf.seek(0)
    lines = [ln for ln in buf.read().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_envelope_fields_seq_ts_schema_version() -> None:
    buf = io.StringIO()
    printer = NdjsonEventPrinter(stream=buf)
    printer({"type": "thought", "text": "first"})
    printer({"type": "tool_call", "name": "list_files"})
    printer({"type": "tool_result", "name": "list_files", "success": True})
    printer({"type": "answer", "text": "done"})

    events = _drain(buf)
    assert [e["seq"] for e in events] == [1, 2, 3, 4], (
        f"seq must be 1-indexed and monotonic; got {[e['seq'] for e in events]}"
    )
    for e in events:
        assert e["schema_version"] == NDJSON_SCHEMA_VERSION
        # datetime.fromisoformat accepts '+00:00' timezone suffix.
        datetime.fromisoformat(e["ts"])
    # Timestamps strictly non-decreasing (monotonic or equal for a burst).
    ts_list = [e["ts"] for e in events]
    assert ts_list == sorted(ts_list)


def test_envelope_does_not_mutate_caller_event() -> None:
    """Printer must take a shallow copy before injecting envelope fields —
    the caller's dict must be untouched so downstream consumers that hold the
    original reference are not surprised by new keys."""
    buf = io.StringIO()
    printer = NdjsonEventPrinter(stream=buf)
    caller_event = {"type": "thought", "text": "first"}
    printer(caller_event)
    assert "seq" not in caller_event
    assert "ts" not in caller_event
    assert "schema_version" not in caller_event


def test_seq_resets_per_printer_instance() -> None:
    """CF-4: a new printer (new process / new run) starts at seq=1 again."""
    buf1 = io.StringIO()
    printer1 = NdjsonEventPrinter(stream=buf1)
    printer1({"type": "thought", "text": "a"})
    printer1({"type": "thought", "text": "b"})
    assert _drain(buf1)[-1]["seq"] == 2

    buf2 = io.StringIO()
    printer2 = NdjsonEventPrinter(stream=buf2)
    printer2({"type": "thought", "text": "fresh"})
    assert _drain(buf2)[0]["seq"] == 1


# --------------------------------------------------------------------------
# 2. --format jsonl is accepted as an alias for --format ndjson.
# --------------------------------------------------------------------------


def test_cli_accepts_format_jsonl_alias() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["--format", "jsonl", "dummy task"])
    assert args.format == "jsonl"


def test_cli_accepts_format_ndjson_literal() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(["--format", "ndjson", "dummy task"])
    assert args.format == "ndjson"


def test_cli_rejects_unknown_format() -> None:
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--format", "pretty", "dummy task"])


# --------------------------------------------------------------------------
# 3. CF-4 guard — no --format flag means no NDJSON envelope keys appear.
# --------------------------------------------------------------------------


def test_cf4_no_format_flag_produces_no_ndjson() -> None:
    """When neither --format ndjson nor --format jsonl is passed, args.format
    must default to None so the CLI does NOT instantiate NdjsonEventPrinter.
    This guards the pre-O6 stdout behavior for callers who never opt in."""
    parser = build_arg_parser()
    args = parser.parse_args(["some prompt"])
    assert args.format is None
