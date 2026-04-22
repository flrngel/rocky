from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from rocky.version import __version__


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_root_import_is_lazy_for_core_agent() -> None:
    sys.modules.pop("rocky", None)
    sys.modules.pop("rocky.core.agent", None)
    importlib.invalidate_caches()

    rocky = importlib.import_module("rocky")
    assert "rocky.core.agent" not in sys.modules

    _ = rocky.strip_markers
    assert "rocky.core.agent" in sys.modules


def test_release_assets_exist() -> None:
    assert (REPO_ROOT / "LICENSE").exists()
    assert (REPO_ROOT / "CHANGELOG.md").exists()
    assert (REPO_ROOT / "CONTRIBUTING.md").exists()
    assert (REPO_ROOT / "docs" / "releases" / f"v{__version__}.md").exists()


def test_license_contains_mit() -> None:
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text


def test_pyproject_points_to_readme_and_has_release_metadata() -> None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - python < 3.11
        import tomli as tomllib  # type: ignore[import-not-found]

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    assert project["readme"] == "README.md"
    assert project["version"] == __version__
    assert project["keywords"]
    assert project["classifiers"]


def test_manifest_includes_release_assets_and_excludes_local_state() -> None:
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for required in [
        "include README.md",
        "include CHANGELOG.md",
        "include LICENSE",
        "recursive-include .agent-testing/specs *.json *.md",
        "prune .rocky",
        "prune .agent-testing/runs",
        "prune .agent-testing/evidence",
        "global-exclude *.py[cod]",
    ]:
        assert required in manifest


def test_gitignore_covers_local_state() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for required in [".rocky/", ".agent-testing/runs/", ".agent-testing/evidence/"]:
        assert required in ignore


def test_agent_testing_tracked_assets_exist() -> None:
    root = REPO_ROOT / ".agent-testing"
    assert (root / "README.md").exists()
    assert (root / "repo-profile.json").exists()
    expected = {
        "sl-memory.json",
        "sl-retrospect.json",
        "sl-promote.json",
        "sl-brief.json",
        "sl-undo.json",
        "sl-all.json",
    }
    actual = {path.name for path in (root / "specs").glob("*.json")}
    assert expected <= actual


def test_agent_testing_repo_profile_matches_version() -> None:
    profile = json.loads((REPO_ROOT / ".agent-testing" / "repo-profile.json").read_text(encoding="utf-8"))
    assert profile["version"] == __version__
