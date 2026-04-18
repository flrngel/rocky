"""Deterministic unit tests for _canonical_args and _args_hash dedup helpers.

Covers the S2 proof card from test-contract.md:
  - Fragment-equality: fetch_url with #fragment-a and #fragment-b collapse to same hash.
  - Token-sort-equality: search_web with differently-ordered tokens collapse to same hash.
  - Distinct-path-inequality: fetch_url with different paths produce different hashes.
  - Other-tool no-op: non-normalised tools are stable and unaffected.
  - Dict-non-mutation: _canonical_args never mutates the caller's dict.
"""

from rocky.core.agent import _args_hash, _canonical_args


class TestCanonicalArgs:
    """Unit tests for _canonical_args helper."""

    def test_fetch_url_strips_fragment(self) -> None:
        """Fragment is removed; base URL is preserved."""
        result = _canonical_args("fetch_url", {"url": "https://example.com/page#section-a"})
        assert result["url"] == "https://example.com/page"

    def test_fetch_url_no_fragment_unchanged(self) -> None:
        """URL without fragment is returned as-is."""
        args = {"url": "https://example.com/page"}
        result = _canonical_args("fetch_url", args)
        assert result["url"] == "https://example.com/page"

    def test_fetch_url_does_not_mutate_input(self) -> None:
        """Original dict is never mutated when fragment is present."""
        original_url = "https://example.com/page#section-a"
        args = {"url": original_url}
        _ = _canonical_args("fetch_url", args)
        assert args["url"] == original_url, "input dict was mutated"

    def test_search_web_sorts_tokens(self) -> None:
        """Query tokens are lowercased and sorted."""
        result = _canonical_args("search_web", {"query": "best wireless earphones 2025"})
        assert result["query"] == "2025 best earphones wireless"

    def test_search_web_already_sorted_unchanged(self) -> None:
        """Already-sorted query produces the same normalized form."""
        args = {"query": "2025 best earphones wireless"}
        result = _canonical_args("search_web", args)
        assert result["query"] == "2025 best earphones wireless"

    def test_search_web_does_not_mutate_input(self) -> None:
        """Original dict is never mutated when query normalization applies."""
        original_query = "best wireless earphones 2025"
        args = {"query": original_query}
        _ = _canonical_args("search_web", args)
        assert args["query"] == original_query, "input dict was mutated"

    def test_other_tool_identity(self) -> None:
        """Non-normalised tools return the same dict object (identity)."""
        args = {"cmd": "ls"}
        result = _canonical_args("run_shell", args)
        assert result is args, "expected identity return for unrecognised tool"

    def test_other_tool_does_not_mutate_input(self) -> None:
        """Dict is not mutated for unrecognised tools."""
        args = {"cmd": "ls"}
        _ = _canonical_args("run_shell", args)
        assert args == {"cmd": "ls"}


class TestArgsHash:
    """Unit tests for _args_hash function (S2 proof assertions)."""

    # S2 assertion 1: fragment-equality
    def test_fragment_variants_produce_same_hash(self) -> None:
        """fetch_url args differing only in #fragment collapse to the same hash."""
        hash_a = _args_hash("fetch_url", {"url": "https://example.com/page#section-a"})
        hash_b = _args_hash("fetch_url", {"url": "https://example.com/page#section-b"})
        assert hash_a == hash_b, (
            f"Expected equal hashes for fragment variants; got {hash_a!r} vs {hash_b!r}"
        )

    # S2 assertion 2: token-sort-equality
    def test_token_reorder_produces_same_hash(self) -> None:
        """search_web args with differently-ordered query tokens collapse to the same hash."""
        hash_ordered = _args_hash("search_web", {"query": "best wireless earphones 2025"})
        hash_reordered = _args_hash("search_web", {"query": "earphones 2025 best wireless"})
        assert hash_ordered == hash_reordered, (
            f"Expected equal hashes for token-reordered queries; "
            f"got {hash_ordered!r} vs {hash_reordered!r}"
        )

    # S2 assertion 3: distinct-path-inequality
    def test_distinct_paths_produce_different_hashes(self) -> None:
        """fetch_url args with different paths produce different hashes."""
        hash_a = _args_hash("fetch_url", {"url": "https://example.com/a"})
        hash_b = _args_hash("fetch_url", {"url": "https://example.com/b"})
        assert hash_a != hash_b, (
            "Expected different hashes for distinct URL paths; both hashed to same value"
        )

    # S2 assertion 4a: other-tool no-op stability
    def test_other_tool_same_args_same_hash(self) -> None:
        """Same args for an unrecognised tool produce identical hashes on repeated calls."""
        hash_1 = _args_hash("run_shell", {"cmd": "ls"})
        hash_2 = _args_hash("run_shell", {"cmd": "ls"})
        assert hash_1 == hash_2

    # S2 assertion 4b: other-tool no-op differentiation
    def test_other_tool_different_args_different_hash(self) -> None:
        """Different args for an unrecognised tool produce different hashes."""
        hash_ls = _args_hash("run_shell", {"cmd": "ls"})
        hash_la = _args_hash("run_shell", {"cmd": "ls -la"})
        assert hash_ls != hash_la

    def test_canonical_args_does_not_mutate_for_fetch_url(self) -> None:
        """Calling _canonical_args for fetch_url leaves the original dict intact."""
        args = {"url": "https://example.com/page#section-a"}
        _ = _canonical_args("fetch_url", args)
        assert args == {"url": "https://example.com/page#section-a"}, (
            "_canonical_args mutated the incoming fetch_url dict"
        )

    def test_canonical_args_does_not_mutate_for_search_web(self) -> None:
        """Calling _canonical_args for search_web leaves the original dict intact."""
        args = {"query": "best wireless earphones 2025"}
        _ = _canonical_args("search_web", args)
        assert args == {"query": "best wireless earphones 2025"}, (
            "_canonical_args mutated the incoming search_web dict"
        )

    def test_canonical_args_does_not_mutate_for_other_tool(self) -> None:
        """Calling _canonical_args for an unrecognised tool leaves the original dict intact."""
        args = {"cmd": "ls"}
        _ = _canonical_args("run_shell", args)
        assert args == {"cmd": "ls"}, (
            "_canonical_args mutated the incoming run_shell dict"
        )
