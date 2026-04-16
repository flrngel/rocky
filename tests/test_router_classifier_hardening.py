Status: DONE
"""Router lexical classifier hardening tests (O1).

Covers C1-classifier: prose uses of | and descriptive mentions of .md/.py/.json
must not false-positive to repo/shell_execution.

Sensitivity-witness procedure is recorded in:
  docs/xlfg/runs/20260416-014309-rocky-issues-resolution/evidence/O1/revert-witness.txt
"""

import pytest

from rocky.core.router import Router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_router = Router()

REPO_OR_SHELL = {"repo/shell_execution", "repo/general", "repo/shell_inspection", "local/runtime_inspection"}


def _route_signature(prompt: str) -> str:
    return _router._lexical_route(prompt).task_signature


# ---------------------------------------------------------------------------
# A -- Prose | must NOT route as shell/repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "List column headers: Name | Value | Count",
        "Fields: ID | Status | Owner",
        "Table structure -- Column | Type | Nullable",
        "Format options are: A | B | C",
    ],
)
def test_prose_pipe_does_not_route_as_shell_or_repo(prompt: str) -> None:
    """A bare | used as a prose data-separator must not trigger shell/repo classification."""
    sig = _route_signature(prompt)
    assert sig not in REPO_OR_SHELL, (
        f"Prose pipe prompt {prompt!r} incorrectly classified as {sig!r}. "
        f"A | between column-header words is not a shell pipe."
    )


# ---------------------------------------------------------------------------
# B -- .md/.py in descriptive prose must NOT route as repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "Explain what a README.md document is for",
        "Describe the purpose of CHANGELOG.md files",
        "What does a Python .py file contain?",
        "What is the purpose of a config.json file?",
        "Tell me about package.json structure",
    ],
)
def test_descriptive_extension_mention_does_not_route_as_repo(prompt: str) -> None:
    """.md/.py/.json mentioned in a descriptive/explanatory sentence must not be repo."""
    sig = _route_signature(prompt)
    # Must not be repo/shell_execution specifically (the worst misroute)
    assert sig != "repo/shell_execution", (
        f"Descriptive prompt {prompt!r} classified as repo/shell_execution -- "
        f"extension mention in explanatory prose is not a repo operation."
    )
    # Should be research or conversation, not repo family
    assert not sig.startswith("repo/"), (
        f"Descriptive prompt {prompt!r} classified as {sig!r} -- "
        f"descriptive mention of a file extension should route as research or conversation, not repo."
    )


# ---------------------------------------------------------------------------
# C -- Negative controls: legitimate shell must still route as shell/repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "cat /etc/hosts | grep local",
        "ls -la && rm tmp.txt",
        "git log | head -5",
        "find . -name '*.py' | xargs wc -l",
        "curl https://example.com | jq '.data'",
    ],
)
def test_legitimate_shell_still_routes_as_shell(prompt: str) -> None:
    """Tightened classifier must not over-relax: real shell commands must stay shell-class."""
    sig = _route_signature(prompt)
    assert sig in REPO_OR_SHELL, (
        f"Shell command prompt {prompt!r} was classified as {sig!r} instead of a shell/repo class. "
        f"The classifier is over-relaxed."
    )


# ---------------------------------------------------------------------------
# D -- CF-4 regression: neutral research prompt unaffected
# ---------------------------------------------------------------------------


def test_neutral_research_prompt_routes_as_research() -> None:
    """A neutral research prompt must not be pulled into shell/repo by the tightened classifier."""
    prompt = "Summarize the latest quarterly trends"
    sig = _route_signature(prompt)
    assert sig not in REPO_OR_SHELL, (
        f"Neutral research prompt classified as {sig!r}. "
        f"Tightening must not affect non-pipe, non-extension prompts."
    )
    # Should be research or conversation
    assert sig.startswith("research/") or sig.startswith("conversation/"), (
        f"Expected research/* or conversation/* for {prompt!r}, got {sig!r}."
    )


# ---------------------------------------------------------------------------
# E -- Load-bearing tests referenced (run by DONE_CHECK command)
# ---------------------------------------------------------------------------
# The following external test suites MUST remain green.  They are verified by
# the DONE_CHECK command:
#   pytest tests/test_route_intersection.py  (5 teach-over-tagging guard tests)
#   pytest tests/test_agent_runtime.py::test_learned_tool_refusal_policy_can_upgrade_conversation_route_to_research
#
# No pytest.skip here -- the DONE_CHECK runs them directly.
