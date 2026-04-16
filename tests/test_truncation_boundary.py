Status: DONE
"""Tests for O7: Line-boundary truncation + structured truncation marker.

Pure function tests — no mocks, no provider calls.
"""
import inspect
import re

from rocky.util.text import truncate


MARKER_PREFIX = "[rocky-truncated:"
OLD_MARKER_PREFIX = "... [truncated"


def extract_omitted_count(text: str) -> int:
    """Extract the integer N from '[rocky-truncated: N chars omitted]'."""
    m = re.search(r"\[rocky-truncated:\s*(\d+)\s+chars omitted\]", text)
    assert m is not None, f"marker not found in: {text!r}"
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Line-boundary snap
# ---------------------------------------------------------------------------

def test_line_boundary_snap_keeps_up_to_last_newline() -> None:
    """truncate snaps the cut point to the last newline before the char limit.

    original = "alpha\\nbeta\\n" + "x"*50  (len=61)
    limit=40 => keep=8 => rfind('\\n',0,8)=5 -> keep_end=6 -> kept="alpha\\n"
    """
    original = "alpha\nbeta\n" + "x" * 50  # len=61
    limit = 40  # keep=8, rfind('\n',0,8)=5, keep_end=6, kept="alpha\n"
    result = truncate(original, limit)

    # kept portion must contain "alpha\n"
    assert "alpha\n" in result, f"expected 'alpha\\n' in {result!r}"
    # nothing from "beta" or later should appear before the marker
    kept_prefix = result.split(MARKER_PREFIX)[0]
    assert "beta" not in kept_prefix, f"beta should be truncated, got kept: {kept_prefix!r}"
    assert MARKER_PREFIX in result, f"new marker missing in {result!r}"
    assert OLD_MARKER_PREFIX not in result, f"old marker must not appear in {result!r}"


def test_line_boundary_snap_accurate_char_count() -> None:
    """The omitted count N equals len(original) - len(kept_prefix)."""
    original = "alpha\nbeta\n" + "x" * 50  # len=61
    limit = 40  # kept="alpha\n" (len=6)
    result = truncate(original, limit)

    # result = "alpha\n" + "[rocky-truncated: ...]"
    # split on MARKER_PREFIX: ["alpha\n", "55 chars omitted]"]
    kept_prefix = result.split(MARKER_PREFIX)[0]
    expected_omitted = len(original) - len(kept_prefix)
    extracted_n = extract_omitted_count(result)
    assert extracted_n == expected_omitted, (
        f"omitted count mismatch: marker says {extracted_n}, "
        f"expected {expected_omitted} (original={len(original)}, kept={len(kept_prefix)!r})"
    )


# ---------------------------------------------------------------------------
# Degenerate single-line (keep=0)
# ---------------------------------------------------------------------------

def test_degenerate_no_newline_small_limit() -> None:
    """No newline anywhere, keep=0: function returns marker with full original length."""
    original = "abcdefghij"
    # limit=6 => keep=max(0,6-32)=0. rfind('\n',0,0)=-1. keep_end=0. kept="".
    result = truncate(original, 6)

    assert MARKER_PREFIX in result, f"marker missing: {result!r}"
    assert OLD_MARKER_PREFIX not in result
    n = extract_omitted_count(result)
    assert n == len(original), f"expected omitted={len(original)}, got {n}"


def test_degenerate_no_newline_realistic() -> None:
    """No newline before keep position: fall back to char cut at keep.

    original = "abcdefghij_and_more_content_no_newlines" (len=39)
    limit=38 => keep=6. rfind('\\n',0,6)=-1. keep_end=6. kept=original[:6]="abcdef".
    """
    original = "abcdefghij_and_more_content_no_newlines"
    limit = 38  # keep=6, no newline -> kept=original[:6], suffix="\n"
    result = truncate(original, limit)

    assert MARKER_PREFIX in result
    # kept_prefix is everything before the marker; strip the trailing \n suffix added by function
    kept_part = result.split(MARKER_PREFIX)[0].rstrip("\n")
    assert kept_part == original[:6], (
        f"expected char-cut at 6 -> {original[:6]!r}, got {kept_part!r}"
    )


# ---------------------------------------------------------------------------
# No-truncation (CF-4 parity)
# ---------------------------------------------------------------------------

def test_no_truncation_returns_unchanged() -> None:
    """Inputs within limit are returned unchanged, with no marker."""
    text = "short"
    result = truncate(text, 4000)
    assert result == text
    assert MARKER_PREFIX not in result


def test_exact_limit_returns_unchanged() -> None:
    """Input exactly at limit is not truncated."""
    text = "x" * 4000
    assert truncate(text, 4000) == text


def test_return_type_is_str() -> None:
    """Function always returns str regardless of input length."""
    assert isinstance(truncate("a" * 5000), str)
    assert isinstance(truncate("tiny"), str)


def test_default_limit_signature() -> None:
    """Signature: default limit=4000 is preserved."""
    sig = inspect.signature(truncate)
    params = sig.parameters
    assert "limit" in params
    assert params["limit"].default == 4000


# ---------------------------------------------------------------------------
# Marker format
# ---------------------------------------------------------------------------

def test_marker_on_its_own_line() -> None:
    """The marker appears on its own line (preceded by \\n when kept has no trailing newline)."""
    original = "abcdefghij_and_more_content_no_newlines"
    limit = 38  # keep=6, no newline -> kept="abcdef", suffix="\n"
    result = truncate(original, limit)
    lines = result.split("\n")
    marker_lines = [ln for ln in lines if MARKER_PREFIX in ln]
    assert len(marker_lines) == 1, f"expected exactly one marker line, got: {lines!r}"
    assert marker_lines[0].strip().startswith(MARKER_PREFIX)


def test_no_double_newline_when_kept_ends_with_newline() -> None:
    """When the kept prefix ends with \\n, no extra \\n is added before the marker."""
    original = "alpha\nbeta\n" + "x" * 50
    limit = 40  # kept = "alpha\n"
    result = truncate(original, limit)
    assert "\n\n" not in result, f"double newline found in {result!r}"
