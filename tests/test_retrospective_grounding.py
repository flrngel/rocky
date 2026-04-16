Status: DONE
"""O5 — Retrospective evidence grounding.

Tests that ground_evidence_citations correctly filters retro evidence items
and that _auto_self_reflect applies the filter before persisting via
StudentStore.
"""

import inspect
import types
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import rocky.app
from rocky.util.evidence import ground_evidence_citations


# ---------------------------------------------------------------------------
# Unit tests — ground_evidence_citations directly
# ---------------------------------------------------------------------------


def test_empty_payload_drops_citation() -> None:
    """Citation is ungrounded when the only tool event has empty stdout."""
    result = ground_evidence_citations(
        ["config.toml present"],
        [{"stdout": ""}],
        direction="retro",
    )
    assert result == []


def test_empty_payload_drops_schema_citation() -> None:
    """Another empty-payload case — schema.json citation dropped."""
    result = ground_evidence_citations(
        ["schema.json mentioned"],
        [{"stdout": ""}],
        direction="retro",
    )
    assert result == []


def test_matching_token_keeps_citation() -> None:
    """Citation token 'config.toml' overlaps with payload — kept."""
    result = ground_evidence_citations(
        ["config.toml found"],
        [{"stdout": "config.toml content here"}],
        direction="retro",
    )
    assert result == ["config.toml found"]


def test_no_tool_events_drops_all() -> None:
    """With no tool events at all, all citations are dropped."""
    result = ground_evidence_citations(
        ["some claim"],
        [],
        direction="retro",
    )
    assert result == []


def test_none_tool_events_drops_all() -> None:
    """None tool_events treated as empty — all citations dropped."""
    result = ground_evidence_citations(
        ["some claim"],
        None,
        direction="retro",
    )
    assert result == []


def test_partial_filter_keeps_only_overlapping() -> None:
    """Only citations with token overlap are kept; others are dropped."""
    result = ground_evidence_citations(
        ["auth middleware owns session state", "database connection pooled"],
        [{"stdout": "the auth middleware in module_x owns session state"}],
        direction="retro",
    )
    assert "auth middleware owns session state" in result
    assert "database connection pooled" not in result


def test_positive_case_auth_middleware() -> None:
    """Positive case: auth middleware citation passes through."""
    result = ground_evidence_citations(
        ["auth middleware owns session state"],
        [{"stdout": "the auth middleware in module_x owns session state"}],
        direction="retro",
    )
    assert result == ["auth middleware owns session state"]


# ---------------------------------------------------------------------------
# Integration — _auto_self_reflect filters evidence
# ---------------------------------------------------------------------------


def _make_fake_runtime():
    """Build a minimal fake RockyRuntime with enough attrs for _auto_self_reflect."""
    rt = object.__new__(rocky.app.RockyRuntime)

    # config: learning enabled so _should_self_reflect returns True
    learning_config = MagicMock()
    learning_config.enabled = True
    learning_config.auto_self_reflection_enabled = True
    config = MagicMock()
    config.learning = learning_config
    rt.config = config

    # ledger: is_lineage_rolled_back always False
    ledger = MagicMock()
    ledger.is_lineage_rolled_back.return_value = False
    ledger.register_artifact = MagicMock()
    rt.ledger = ledger

    # provider_registry: primary() returns None
    provider_registry = MagicMock()
    provider_registry.primary.return_value = None
    rt.provider_registry = provider_registry

    # agent: needs last_trace attr
    rt.agent = MagicMock()

    # refresh_knowledge and _persist_trace_update are no-ops in tests
    rt.refresh_knowledge = MagicMock()
    rt._persist_trace_update = MagicMock()

    # _active_teach_lineages: returns empty iterable (wrapped in try/except anyway)
    rt._active_teach_lineages = MagicMock(return_value=[])

    return rt


def _make_agent_response(tool_events: list[dict[str, Any]], text: str = "answer") -> MagicMock:
    """Build a fake AgentResponse with a realistic trace."""
    from rocky.core.router import Lane, RouteDecision, TaskClass

    route = MagicMock()
    route.lane = Lane.STANDARD
    route.task_signature = "repo/test"

    response = MagicMock()
    response.text = text
    response.route = route
    response.trace = {
        "tool_events": tool_events,
        "thread": {},
    }
    return response


def test_auto_self_reflect_filters_ungrounded_evidence(tmp_path) -> None:
    """Ungrounded evidence is dropped before StudentStore.add is called."""
    rt = _make_fake_runtime()

    # Fake retrospective returned by retrospect_episode (already dict form)
    fake_retro_record = {
        "title": "Test retro",
        "summary": "A test summary",
        "keywords": ["test"],
        "evidence": ["config.toml was empty when read"],
        "task_signature": "repo/test",
        "thread_id": None,
        "failure_class": None,
        "repeat_next_time": [],
        "avoid_next_time": [],
        "recall_when": [],
        "confidence": 0.7,
        "should_persist": True,
        "task_family": "repo",
    }

    fake_result = {
        "persisted": True,
        "artifact_path": str(tmp_path / "retro.md"),
        "retrospective": fake_retro_record,
        "text": "# Self retrospective\n\nA test summary",
    }

    captured_evidence: list[list[str]] = []

    def fake_student_store_add(kind, title, text, **kwargs):
        # We can't easily intercept `retrospective` dict at this point;
        # we capture it via the surrounding scope mutation test below.
        return {"ok": True, "entry": {"path": str(tmp_path / "note.md")}}

    learning_manager = MagicMock()
    learning_manager.retrospect_episode.return_value = fake_result
    rt.learning_manager = learning_manager

    student_store = MagicMock()
    student_store.add.side_effect = fake_student_store_add
    rt.student_store = student_store

    # One tool event with empty stdout — so "config.toml" citation is ungrounded
    tool_events = [{"stdout": ""}]
    response = _make_agent_response(tool_events)

    # Capture what retrospective["evidence"] looks like after filtering
    # by patching ground_evidence_citations to record its output
    original_gec = ground_evidence_citations
    gec_results: list[list[str]] = []

    def recording_gec(items, events, *, direction="retro", **kwargs):
        result = original_gec(items, events, direction=direction, **kwargs)
        gec_results.append(result)
        return result

    with patch("rocky.app.ground_evidence_citations", side_effect=recording_gec):
        rocky.app.RockyRuntime._auto_self_reflect(rt, "test prompt", response)

    # The filter should have been called and returned []
    assert gec_results, "ground_evidence_citations was not called"
    assert gec_results[0] == [], "ungrounded evidence was not dropped"

    # student_store.add should still have been called (retro persisted, just empty evidence)
    assert student_store.add.called, "student_store.add should have been called"


def test_auto_self_reflect_preserves_summary_and_keywords(tmp_path) -> None:
    """Summary and keywords pass through unchanged even when evidence is filtered."""
    rt = _make_fake_runtime()

    fake_retro_record = {
        "title": "Preserved fields test",
        "summary": "The important summary",
        "keywords": ["alpha", "beta"],
        "evidence": ["nonexistent file was read"],
        "task_signature": "repo/test",
        "thread_id": None,
        "failure_class": None,
        "repeat_next_time": [],
        "avoid_next_time": [],
        "recall_when": [],
        "confidence": 0.8,
        "should_persist": True,
        "task_family": "repo",
    }

    fake_result = {
        "persisted": True,
        "artifact_path": str(tmp_path / "retro2.md"),
        "retrospective": fake_retro_record,
        "text": "# Self retrospective\n\nThe important summary",
    }

    learning_manager = MagicMock()
    learning_manager.retrospect_episode.return_value = fake_result
    rt.learning_manager = learning_manager

    captured_add_kwargs: list[dict] = []

    def capturing_add(kind, title, text, **kwargs):
        captured_add_kwargs.append({"kind": kind, "title": title, "text": text, **kwargs})
        return {"ok": True, "entry": {"path": str(tmp_path / "note2.md")}}

    student_store = MagicMock()
    student_store.add.side_effect = capturing_add
    rt.student_store = student_store

    # No tool events -> all evidence is filtered
    response = _make_agent_response([])
    rocky.app.RockyRuntime._auto_self_reflect(rt, "test prompt", response)

    assert captured_add_kwargs, "student_store.add was not called"
    call = captured_add_kwargs[0]
    # keywords preserved in tags
    assert "alpha" in call.get("tags", [])
    assert "beta" in call.get("tags", [])
    # title and summary text passed through
    assert "Preserved fields test" in call["title"]
    assert "important summary" in call["text"]


def test_auto_self_reflect_keeps_valid_evidence(tmp_path) -> None:
    """Grounded evidence (overlapping tokens) passes through."""
    rt = _make_fake_runtime()

    fake_retro_record = {
        "title": "Valid evidence retro",
        "summary": "Module X was verified",
        "keywords": ["module_x"],
        "evidence": ["auth middleware owns session state"],
        "task_signature": "repo/test",
        "thread_id": None,
        "failure_class": None,
        "repeat_next_time": [],
        "avoid_next_time": [],
        "recall_when": [],
        "confidence": 0.9,
        "should_persist": True,
        "task_family": "repo",
    }

    fake_result = {
        "persisted": True,
        "artifact_path": str(tmp_path / "retro3.md"),
        "retrospective": fake_retro_record,
        "text": "# Self retrospective\n\nModule X was verified",
    }

    learning_manager = MagicMock()
    learning_manager.retrospect_episode.return_value = fake_result
    rt.learning_manager = learning_manager

    gec_results: list[list[str]] = []
    original_gec = ground_evidence_citations

    def recording_gec(items, events, *, direction="retro", **kwargs):
        result = original_gec(items, events, direction=direction, **kwargs)
        gec_results.append(result)
        return result

    student_store = MagicMock()
    student_store.add.return_value = {"ok": True, "entry": {"path": str(tmp_path / "note3.md")}}
    rt.student_store = student_store

    # Tool event containing matching tokens
    tool_events = [{"stdout": "the auth middleware in module_x owns session state"}]
    response = _make_agent_response(tool_events)

    with patch("rocky.app.ground_evidence_citations", side_effect=recording_gec):
        rocky.app.RockyRuntime._auto_self_reflect(rt, "test prompt", response)

    assert gec_results, "ground_evidence_citations was not called"
    assert "auth middleware owns session state" in gec_results[0], (
        f"valid citation was incorrectly dropped; got {gec_results[0]}"
    )


# ---------------------------------------------------------------------------
# Structural guard — source-level anchor
# ---------------------------------------------------------------------------


def test_auto_self_reflect_calls_ground_evidence_citations() -> None:
    """_auto_self_reflect source must contain a call to ground_evidence_citations."""
    src = inspect.getsource(rocky.app.RockyRuntime._auto_self_reflect)
    assert "ground_evidence_citations" in src, (
        "_auto_self_reflect does not call ground_evidence_citations — "
        "the O5 filter has been removed or renamed"
    )


def test_app_module_imports_ground_evidence_citations() -> None:
    """rocky.app module must import ground_evidence_citations at module level."""
    src = inspect.getsource(rocky.app)
    assert "ground_evidence_citations" in src, (
        "rocky.app does not import ground_evidence_citations"
    )
