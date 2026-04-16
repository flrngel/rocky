"""
O19 — Documentation + version bump.

The v1.2.0 docs bundle (follow-up §10) must cover 8 user-facing topics in
``README.md`` plus reference ``answer_bounded_text`` for integrators. Version
must be consistent across ``src/rocky/__init__.py`` and ``pyproject.toml`` at
``1.2.0`` — current is ``1.1.0`` and this batch ships feature additions so a
minor bump is required.
"""
from __future__ import annotations

from pathlib import Path

import rocky


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_readme_covers_follow_up_topics() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    required_topics = [
        "--route",
        "--tools",
        "--state-dir",
        "--format ndjson",
        "rocky stats",
        "--freeze",
        "tool_output_limits",
        "semantic_enabled",
    ]
    missing = [topic for topic in required_topics if topic not in readme]
    assert missing == [], f"README is missing follow-up §10 topics: {missing}"


def test_readme_references_answer_bounded_text() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "answer_bounded_text" in readme, (
        "README must reference answer_bounded_text as the integration surface."
    )


def test_version_bumped_to_one_two_zero() -> None:
    assert rocky.__version__ == "1.2.0", (
        f"Rocky version must be 1.2.0 for this batch; got {rocky.__version__!r}"
    )


def test_version_matches_between_init_and_pyproject() -> None:
    init_version = rocky.__version__

    try:
        import tomllib
    except ImportError:  # pragma: no cover - python < 3.11
        import tomli as tomllib  # type: ignore[import-not-found]

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    py_version = pyproject["project"]["version"]
    assert init_version == py_version, (
        f"src/rocky/__init__.py version {init_version!r} must match "
        f"pyproject.toml version {py_version!r}"
    )


def test_freeze_retro_suppression_mentioned() -> None:
    """The follow-up §10 call-out specifically requires that README mention
    that `--freeze` implicitly ignores retrospectives — it is a P2 invariant
    operator-facing users need to know about."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "freeze" in readme
    assert "retrospective" in readme or "retros" in readme
