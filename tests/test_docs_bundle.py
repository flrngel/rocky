"""Documentation and release-bundle drift guards."""
from __future__ import annotations

from pathlib import Path

import rocky
from rocky.version import __version__


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_readme_covers_core_operator_topics() -> None:
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
        "docs/scenarios.md",
        "docs/capabilities.json",
        f"docs/releases/v{__version__}.md",
    ]
    missing = [topic for topic in required_topics if topic not in readme]
    assert missing == [], f"README is missing operator/release topics: {missing}"


def test_readme_references_answer_bounded_text() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "answer_bounded_text" in readme, (
        "README must reference answer_bounded_text as the integration surface."
    )


def test_versions_match_between_package_and_pyproject() -> None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - python < 3.11
        import tomli as tomllib  # type: ignore[import-not-found]

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    py_version = pyproject["project"]["version"]
    assert rocky.__version__ == py_version == __version__


def test_readme_status_mentions_current_version() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Active development. v{__version__}." in readme


def test_release_note_for_current_version_exists() -> None:
    release_note = REPO_ROOT / "docs" / "releases" / f"v{__version__}.md"
    assert release_note.exists(), f"missing release note for {__version__}: {release_note}"


def test_freeze_retro_suppression_mentioned() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "freeze" in readme
    assert "retrospective" in readme or "retros" in readme
