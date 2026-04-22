from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rocky.version import __version__


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_upgrade_audit_has_exactly_one_hundred_fixed_items() -> None:
    path = REPO_ROOT / "docs" / "upgrade" / f"rocky-v{__version__}-audit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    issues = payload["issues"]
    assert payload["version"] == __version__
    assert len(issues) == 100
    assert all(issue["status"] == "fixed" for issue in issues)


def test_upgrade_audit_balances_categories() -> None:
    path = REPO_ROOT / "docs" / "upgrade" / f"rocky-v{__version__}-audit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    counts = Counter(issue["category"] for issue in payload["issues"])
    assert counts == {
        "release-packaging": 20,
        "repo-hygiene": 20,
        "public-api-consistency": 20,
        "docs-operator-ux": 20,
        "tests-guardrails": 20,
    }


def test_upgrade_audit_markdown_exists() -> None:
    path = REPO_ROOT / "docs" / "upgrade" / f"rocky-v{__version__}-audit.md"
    text = path.read_text(encoding="utf-8")
    assert "100 concrete repository problems" in text
    assert "P100" in text
