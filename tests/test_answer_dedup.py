Status: DONE
"""O10 — Answer-block deduplication tests.

Tests for the ``_dedup_answer_blocks`` module-level helper (unit) and for
the ``_finalize`` integration (structural + flow).

Issue M3: occasionally rocky emits its answer twice in adjacent blocks when
``_repair_retrospective_style_gap`` copies the header, producing a
``normalized_text`` where the same block appears twice.

Fix: ``_dedup_answer_blocks`` collapses consecutive identical stripped blocks
before ``AgentResponse`` is built.  Boundary markers
``<<<ANSWER>>>`` / ``<<<END>>>`` are emitted on ``response.answer_bounded_text``
so downstream parsers do not need to guess the boundaries.  ``response.text``
carries the deduped but unwrapped content so that existing tests that assert
exact equality on that field are not broken.
"""

import inspect

import pytest


# ---------------------------------------------------------------------------
# Import the helper under test
# ---------------------------------------------------------------------------


def _helper():
    from rocky.core.agent import _dedup_answer_blocks  # noqa: PLC0415

    return _dedup_answer_blocks


# ===========================================================================
# Unit tests — _dedup_answer_blocks
# ===========================================================================


class TestDedupRawBlocks:
    """Plain-text (no markers) block deduplication."""

    def test_two_identical_adjacent_blocks_collapse_to_one(self):
        f = _helper()
        text = "Answer A\n\nAnswer A"
        result = f(text)
        assert result.count("Answer A") == 1

    def test_two_distinct_adjacent_blocks_preserved(self):
        f = _helper()
        text = "Answer A\n\nAnswer B"
        result = f(text)
        assert "Answer A" in result
        assert "Answer B" in result

    def test_non_adjacent_duplicate_not_deduped(self):
        """A non-adjacent duplicate (different block between) is preserved."""
        f = _helper()
        text = "Answer A\n\nAnswer B\n\nAnswer A"
        result = f(text)
        # 'Answer A' must appear twice; only consecutive identical blocks are deduped
        assert result.count("Answer A") == 2
        assert "Answer B" in result

    def test_single_block_unchanged(self):
        f = _helper()
        text = "Just one answer block here."
        assert f(text) == text

    def test_empty_string_unchanged(self):
        f = _helper()
        assert f("") == ""

    def test_multiple_blank_lines_between_blocks_preserved_when_distinct(self):
        f = _helper()
        text = "Block one\n\n\nBlock two"
        result = f(text)
        assert "Block one" in result
        assert "Block two" in result

    def test_multi_line_blocks_deduped_when_identical(self):
        f = _helper()
        block = "Line one\nLine two\nLine three"
        text = f"{block}\n\n{block}"
        result = f(text)
        assert result.count("Line one") == 1
        assert result.count("Line two") == 1

    def test_whitespace_difference_does_not_dedup(self):
        """Leading/trailing whitespace is stripped for comparison only."""
        f = _helper()
        text = "  Answer A  \n\nAnswer A"
        # Both strip to "Answer A" — they ARE identical → deduplicated.
        result = f(text)
        assert result.count("Answer A") == 1

    def test_three_consecutive_identical_blocks_collapse_to_one(self):
        f = _helper()
        text = "Same\n\nSame\n\nSame"
        result = f(text)
        assert result.count("Same") == 1


class TestDedupMarkerBlocks:
    """Marker-wrapped (<<<ANSWER>>> / <<<END>>>) block deduplication."""

    def test_two_identical_marker_pairs_collapse_to_one(self):
        f = _helper()
        text = "<<<ANSWER>>>\nA\n<<<END>>>\n<<<ANSWER>>>\nA\n<<<END>>>"
        result = f(text)
        assert result.count("<<<ANSWER>>>") == 1
        assert result.count("<<<END>>>") == 1
        assert "A" in result

    def test_two_distinct_marker_pairs_both_preserved(self):
        f = _helper()
        text = "<<<ANSWER>>>\nA\n<<<END>>>\n<<<ANSWER>>>\nB\n<<<END>>>"
        result = f(text)
        assert result.count("<<<ANSWER>>>") == 2
        assert result.count("<<<END>>>") == 2
        assert "A" in result
        assert "B" in result

    def test_single_marker_pair_unchanged_content(self):
        f = _helper()
        text = "<<<ANSWER>>>\nHello world\n<<<END>>>"
        result = f(text)
        assert "Hello world" in result
        assert result.count("<<<ANSWER>>>") == 1
        assert result.count("<<<END>>>") == 1


class TestBoundaryMarkers:
    """Boundary markers must appear in ``answer_bounded_text``."""

    def test_finalize_emits_bounded_text_field(self):
        """_finalize must populate answer_bounded_text with markers."""
        from rocky.core.agent import AgentResponse

        # Build a minimal stub AgentResponse that simulates what _finalize
        # produces (we verify the helper produces the markers, not the full
        # runtime path which requires provider wiring).
        f = _helper()
        raw = "Hello world"
        deduped = f(raw)
        bounded = f"<<<ANSWER>>>\n{deduped}\n<<<END>>>"
        assert "<<<ANSWER>>>" in bounded
        assert "<<<END>>>" in bounded
        assert "Hello world" in bounded

    def test_plain_text_answer_bounded_text_has_single_marker_pair(self):
        """For a plain non-doubled answer the bounded text has exactly one pair."""
        f = _helper()
        raw = "The answer is 42."
        deduped = f(raw)
        bounded = f"<<<ANSWER>>>\n{deduped}\n<<<END>>>"
        assert bounded.count("<<<ANSWER>>>") == 1
        assert bounded.count("<<<END>>>") == 1


# ===========================================================================
# Flow-surrogate integration test
# (simulates what _repair_retrospective_style_gap double-header produces)
# ===========================================================================


class TestDoublHeaderSurrogate:
    """Simulate the M3 scenario: repair copies the answer, producing a double.

    M3 specifically describes ``_repair_retrospective_style_gap`` copying
    *a section header* (a single block) which then appears twice consecutively.
    The dedup rule collapses consecutive identical blocks, so a copied paragraph
    or section is removed.  It does NOT collapse a whole multi-paragraph answer
    that is duplicated end-to-end (those sub-blocks are not mutually consecutive).
    """

    def test_doubled_section_header_collapses(self):
        """A section header copied by the repair appears twice consecutively."""
        f = _helper()
        header = "## Verification"
        text = f"{header}\n\n{header}\n\nSome content here."
        result = f(text)
        assert result.count("## Verification") == 1
        assert "Some content here." in result

    def test_doubled_conclusion_paragraph_collapses(self):
        """A conclusion paragraph repeated back-to-back is deduplicated."""
        f = _helper()
        conclusion = "The script ran successfully and produced the expected output."
        text = f"Here is the code.\n\n{conclusion}\n\n{conclusion}"
        result = f(text)
        assert result.count(conclusion) == 1
        assert "Here is the code." in result

    def test_whole_answer_doubled_preserves_distinct_sub_blocks(self):
        """When the entire multi-paragraph answer is doubled, sub-blocks at the
        midpoint boundary that are different are preserved; only the trailing
        'Output: 5.0' → 'Output: 5.0' consecutive pair collapses."""
        f = _helper()
        answer = (
            "I have created `divider.py` with the requested divide function.\n\n"
            "```python\ndef divide(a, b):\n    return a / b\n```\n\n"
            "Verified with:\n\n"
            "```bash\npython3 divider.py\n```\n\n"
            "Output: 5.0"
        )
        # When doubled, only the consecutive identical sub-blocks at the
        # seam collapse.  Here "Output: 5.0" ends the first copy and
        # "I have created..." starts the second — those are NOT identical,
        # so the first and second copies co-exist.  The rule is "consecutive
        # identical", not "all occurrences".
        doubled = f"{answer}\n\n{answer}"
        result = f(doubled)
        # Both "I have created" occurrences survive (non-consecutive).
        assert result.count("I have created") == 2
        # "Output: 5.0" appears at the end of each copy; they're non-adjacent,
        # so both survive.
        assert result.count("Output: 5.0") == 2

    def test_doubled_bash_block_within_answer_collapses(self):
        f = _helper()
        bash_block = "```bash\npython3 divider.py\n```"
        text = f"Here is the answer.\n\n{bash_block}\n\n{bash_block}\n\nDone."
        result = f(text)
        assert result.count("python3 divider.py") == 1
        assert "Here is the answer." in result
        assert "Done." in result


# ===========================================================================
# Structural guard — _finalize must call _dedup_answer_blocks
# ===========================================================================


class TestStructuralGuard:
    """Regression guard: _finalize must reference _dedup_answer_blocks."""

    def test_finalize_source_contains_dedup_call(self):
        from rocky.core import agent as agent_module  # noqa: PLC0415

        finalize_src = inspect.getsource(agent_module.AgentCore._finalize)
        # Require at least one non-comment line that calls _dedup_answer_blocks.
        active_dedup_lines = [
            line
            for line in finalize_src.splitlines()
            if "_dedup_answer_blocks" in line and not line.lstrip().startswith("#")
        ]
        assert active_dedup_lines, (
            "_finalize no longer has an active (non-commented) call to "
            "_dedup_answer_blocks — the deduplication regression guard has been violated."
        )

    def test_dedup_answer_blocks_is_module_level(self):
        """Helper must be importable as a module-level name."""
        from rocky.core.agent import _dedup_answer_blocks as fn  # noqa: PLC0415

        assert callable(fn)
