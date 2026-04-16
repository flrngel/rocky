"""
O11 — Claim extractor relational-claim coverage.

The extractor in :class:`rocky.core.verifiers.VerifierRegistry` combines
quoted strings, multi-word proper nouns, and (new) relational claims
captured by a narrow verb whitelist: ``depends on``, ``relies on``,
``requires``, ``causes``, ``leads to``, ``results in``, ``is built on``,
``is based on``.

Test scenarios use prompts that are **lexically distinct** from the
follow-ups §5.2 example (which is "X depends on Y because Z"). If the
follow-up example were used verbatim, the test would only prove
instruction-following, not generalization (Q2, I3).
"""
from __future__ import annotations

import pytest

from rocky.core.verifiers import VerifierRegistry


@pytest.fixture
def extract():
    reg = VerifierRegistry()
    return reg._extract_claims


# --------------------------------------------------------------------------
# Positive relational-claim coverage.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_substrings",
    [
        # "requires X" — narrow whitelist includes `requires`.
        (
            "The garbage collector requires a stop-the-world pause to reclaim memory.",
            ["requires"],
        ),
        # "causes X" — captures subject/verb/object fragment.
        (
            "Aggressive batching causes checkpoint pressure on the journal.",
            ["causes"],
        ),
        # "relies on X" — multi-word verb phrase.
        (
            "The scheduler relies on monotonic clocks to avoid re-issuing timers.",
            ["relies on"],
        ),
        # "is built on X" — multi-word verb phrase with article.
        (
            "The feature matrix is built on canonical evaluation records.",
            ["is built on"],
        ),
    ],
)
def test_relational_verb_catalog_extracts_claims(extract, text, expected_substrings) -> None:
    claims = extract(text)
    joined = " | ".join(claims).lower()
    for needle in expected_substrings:
        assert needle in joined, (
            f"Expected relational verb {needle!r} to appear in extracted claims; "
            f"got: {claims}"
        )


# --------------------------------------------------------------------------
# Existing proper-noun extraction still works (regression guard).
# --------------------------------------------------------------------------


def test_proper_noun_extraction_still_works(extract) -> None:
    text = "The Microsoft TypeScript team publishes releases on GitHub Trending every quarter."
    claims = extract(text)
    # Multi-word proper-noun run: "Microsoft TypeScript" or "GitHub Trending".
    joined = " | ".join(claims)
    assert any("TypeScript" in c or "Trending" in c for c in claims), (
        f"Existing proper-noun extraction regressed. Claims: {claims}"
    )


# --------------------------------------------------------------------------
# Narrow whitelist — arbitrary verbs do NOT match.
# --------------------------------------------------------------------------


def test_non_whitelisted_verb_does_not_trip_extractor(extract) -> None:
    # "loves" is not on the whitelist; this is a subject/verb/object shape
    # that MUST NOT be matched. Prevents the extractor from flooding claims
    # with arbitrary sentences.
    text = "alice loves bob."
    claims = extract(text)
    lowered = " | ".join(c.lower() for c in claims)
    assert "loves" not in lowered, (
        f"Expected narrow whitelist to reject 'loves'; got: {claims}"
    )
