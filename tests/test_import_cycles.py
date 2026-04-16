"""
O2 — util/evidence.py must not lazy-import from rocky.tool_events.

Before the fix, ``_payload_text`` inside ``rocky.util.evidence`` contained a
function-local ``from rocky.tool_events import tool_event_payload`` to avoid a
module-load-time cycle (``util/`` → ``tool_events`` → ``tools/base``). Static
analysis cannot see such imports, and if ``util/`` were ever extracted to its
own package the deferred import would break at runtime.

This test asserts the lazy-import line is gone and that
``rocky.util.evidence`` loads cleanly at module top without materializing
``rocky.tool_events`` as a prerequisite.
"""
from __future__ import annotations

import importlib
import inspect
import sys


def test_evidence_module_has_no_lazy_tool_events_import() -> None:
    module = importlib.import_module("rocky.util.evidence")
    source = inspect.getsource(module)
    assert "from rocky.tool_events" not in source, (
        "rocky.util.evidence must not import from rocky.tool_events, "
        "including inside function bodies. Lazy imports hide cycles from "
        "static analysis."
    )


def test_evidence_module_imports_without_tool_events() -> None:
    """Loading rocky.util.evidence must not require rocky.tool_events.

    Evict ``rocky.tool_events`` from ``sys.modules`` (if present), re-import
    ``rocky.util.evidence`` with a fresh module cache, and confirm the load
    does not materialize ``rocky.tool_events`` as a side-effect. The util
    module's payload-text helper must stand alone.
    """
    # Remove both modules so re-import is clean.
    for mod_name in ("rocky.util.evidence", "rocky.tool_events"):
        sys.modules.pop(mod_name, None)

    importlib.import_module("rocky.util.evidence")

    assert "rocky.tool_events" not in sys.modules, (
        "Loading rocky.util.evidence should not import rocky.tool_events. "
        "If this fires, the module-top evidence path re-introduced the cycle."
    )


def test_ground_evidence_citations_matches_modern_event_shape() -> None:
    """The ``raw_text`` branch must match tokens from a normalized tool event
    even though tool_events is not imported. Guards against regressing to the
    legacy-only fallback path."""
    from rocky.util.evidence import ground_evidence_citations

    event_modern = {
        "type": "tool_result",
        "name": "fetch_url",
        "raw_text": '{"title": "GitHub Trending", "url": "https://github.com/trending"}',
    }
    kept = ground_evidence_citations(
        ["GitHub Trending — the curated trending repositories page"],
        [event_modern],
        direction="claim",
        min_overlap=2,
    )
    assert kept, "Modern event shape (raw_text) must be visible to grounding."


def test_ground_evidence_citations_matches_legacy_event_shape() -> None:
    """Legacy events that exposed ``stdout``/``stderr`` must still ground."""
    from rocky.util.evidence import ground_evidence_citations

    event_legacy = {
        "type": "tool_result",
        "name": "run_shell_command",
        "stdout": "Listing repositories: microsoft/typescript, rust-lang/rust.\n",
        "stderr": "",
    }
    kept = ground_evidence_citations(
        ["Listing microsoft/typescript repositories"],
        [event_legacy],
        direction="claim",
        min_overlap=2,
    )
    assert kept, "Legacy event shape (stdout) must still ground citations."
