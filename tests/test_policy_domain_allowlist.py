"""
Tests for the per-domain weak-token allowlist in LearnedPolicyRetriever.

SC-1: repo policy with command-only overlap is retrieved after the fix.
SC-2: non-repo (conversation) policy with command-only overlap is NOT retrieved.
SC-3: lexically-diverse repo prompt still retrieves the policy (generalization).
"""

from pathlib import Path

from rocky.learning.policies import LearnedPolicy, LearnedPolicyRetriever


def _repo_policy() -> LearnedPolicy:
    return LearnedPolicy(
        policy_id="repo-shell-guidance",
        scope="project",
        path=Path("/tmp/fake/POLICY.md"),
        body="Use git status before running commands.",
        metadata={
            "task_family": "repo",
            "retrieval": {"keywords": ["command", "shell"]},
            "promotion_state": "promoted",
        },
    )


def test_sc1_repo_command_policy_retrieved() -> None:
    """repo policy with command-only token overlap must be retrieved after domain allowlist fix."""
    policy = _repo_policy()
    retriever = LearnedPolicyRetriever([policy])
    results = retriever.retrieve("run the command", "repo/shell_execution")
    assert any(p.policy_id == "repo-shell-guidance" for p in results), (
        "repo policy with command-only overlap must be retrieved after domain allowlist fix"
    )


def test_sc2_conversation_command_policy_not_retrieved() -> None:
    """conversation policy with command-only overlap must NOT be retrieved (allowlist is repo-scoped)."""
    policy = LearnedPolicy(
        policy_id="conversation-greeting-guidance",
        scope="project",
        path=Path("/tmp/fake/POLICY.md"),
        body="Greet the user warmly before issuing commands.",
        metadata={
            "task_family": "conversation",
            "retrieval": {"keywords": ["command", "greeting"]},
            "promotion_state": "promoted",
        },
    )
    retriever = LearnedPolicyRetriever([policy])
    results = retriever.retrieve("run the command", "conversation/general")
    assert not any(p.policy_id == "conversation-greeting-guidance" for p in results), (
        "conversation policy with command-only overlap must NOT be retrieved (allowlist is repo-scoped)"
    )


def test_sc3_repo_command_policy_generalization() -> None:
    """Lexically-diverse repo prompt must still retrieve the repo policy (generalization check)."""
    policy = _repo_policy()
    retriever = LearnedPolicyRetriever([policy])
    results = retriever.retrieve("execute the command in the shell", "repo/shell_execution")
    assert any(p.policy_id == "repo-shell-guidance" for p in results), (
        "generalization: alternate repo prompt with 'command' token must also retrieve the policy"
    )
