Status: DONE
"""O17 — Tag-extractor hygiene tests.

Four case groups:
  a. Report reproducer (lexically distinct from report wording)
  b. Domain generalization (different vocabulary, same noise class)
  c. LLM-keywords synthesis path (_filter_llm_keywords helper)
  d. CF-4 control (non-noisy inputs pass through unchanged)
"""
import pytest

from rocky.util.text import tokenize_keywords
from rocky.learning.synthesis import _filter_llm_keywords


# ---------------------------------------------------------------------------
# a. Report reproducer
# ---------------------------------------------------------------------------
class TestReportReproducer:
    """Reproduces P6: noise tokens and trailing-punctuation tokens leak into tags."""

    def test_noise_tokens_absent(self) -> None:
        result = tokenize_keywords("not need order. after. product comparison research")
        # stop-words must be absent (both added in O17)
        assert "not" not in result, f"'not' leaked: {result}"
        assert "need" not in result, f"'need' leaked: {result}"

    def test_trailing_punctuation_absent(self) -> None:
        result = tokenize_keywords("not need order. after. product comparison research")
        # raw dotted forms must not appear
        assert "order." not in result, f"'order.' leaked: {result}"
        assert "after." not in result, f"'after.' leaked: {result}"
        # stripped forms with length < 4 must also be absent (after="after" len=5, order="order" len=5)
        # "after" and "order" are 5 chars — they are allowed; only verify the dotted form is gone
        assert "after." not in result

    def test_content_tokens_present(self) -> None:
        result = tokenize_keywords("not need order. after. product comparison research")
        assert "product" in result, f"'product' missing: {result}"
        assert "comparison" in result, f"'comparison' missing: {result}"
        assert "research" in result, f"'research' missing: {result}"

    def test_order_stripped_form_present(self) -> None:
        """'orders.' -> strip dot -> 'orders' -> plural strip -> 'order' added."""
        result = tokenize_keywords("orders.")
        assert "orders" in result, f"'orders' missing: {result}"
        assert "order" in result, f"plural 'order' missing: {result}"


# ---------------------------------------------------------------------------
# b. Domain generalization
# ---------------------------------------------------------------------------
class TestDomainGeneralization:
    """Different vocabulary, same noise class as P6."""

    def test_stop_words_absent(self) -> None:
        result = tokenize_keywords("the for not via per metrics config")
        assert "the" not in result, f"'the' leaked: {result}"
        assert "for" not in result, f"'for' leaked: {result}"
        assert "not" not in result, f"'not' leaked: {result}"
        assert "via" not in result, f"'via' leaked: {result}"
        assert "per" not in result, f"'per' leaked: {result}"

    def test_content_words_present(self) -> None:
        result = tokenize_keywords("the for not via per metrics config")
        assert "metrics" in result, f"'metrics' missing: {result}"
        assert "config" in result, f"'config' missing: {result}"


# ---------------------------------------------------------------------------
# c. LLM-keywords synthesis path
# ---------------------------------------------------------------------------
class TestFilterLlmKeywords:
    """Exercises the _filter_llm_keywords helper that guards the synthesis merge."""

    def test_noise_filtered_out(self) -> None:
        result = _filter_llm_keywords(["not", "need", "product comparison"])
        assert "not" not in result, f"'not' leaked: {result}"
        assert "need" not in result, f"'need' leaked: {result}"

    def test_content_tokens_present(self) -> None:
        result = _filter_llm_keywords(["not", "need", "product comparison"])
        assert "product" in result, f"'product' missing: {result}"
        assert "comparison" in result, f"'comparison' missing: {result}"

    def test_multi_word_keyword_split(self) -> None:
        """'product comparison' should yield both tokens."""
        result = _filter_llm_keywords(["product comparison"])
        assert "product" in result
        assert "comparison" in result

    def test_none_input_returns_empty(self) -> None:
        assert _filter_llm_keywords(None) == set()

    def test_empty_list_returns_empty(self) -> None:
        assert _filter_llm_keywords([]) == set()

    def test_not_filtered(self) -> None:
        result = _filter_llm_keywords(["not"])
        assert "not" not in result, f"'not' leaked: {result}"


# ---------------------------------------------------------------------------
# d. CF-4 control — non-noisy inputs pass through
# ---------------------------------------------------------------------------
class TestControlPassThrough:
    """Non-noisy 4+-char words must survive unchanged."""

    def test_happy_path(self) -> None:
        result = tokenize_keywords("python router config")
        assert "python" in result, f"'python' missing: {result}"
        assert "router" in result, f"'router' missing: {result}"
        assert "config" in result, f"'config' missing: {result}"

    def test_four_char_words_pass(self) -> None:
        """Boundary: exactly 4 chars must pass (>= 4 required)."""
        result = tokenize_keywords("read file tool")
        assert "read" in result, f"'read' missing: {result}"
        assert "file" in result, f"'file' missing: {result}"
        assert "tool" in result, f"'tool' missing: {result}"

    def test_three_char_non_stop_words_blocked(self) -> None:
        """3-char tokens that are not stop-words also blocked by len < 4."""
        result = tokenize_keywords("foo bar baz")
        assert "foo" not in result
        assert "bar" not in result
        assert "baz" not in result
