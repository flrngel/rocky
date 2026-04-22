from __future__ import annotations

import json
from pathlib import Path

from rocky.capabilities import capability_inventory


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_capabilities_json_matches_current_inventory() -> None:
    expected = capability_inventory()
    payload = json.loads((REPO_ROOT / "docs" / "capabilities.json").read_text(encoding="utf-8"))
    assert payload == expected


def test_scenarios_markdown_mentions_every_task_signature_and_command() -> None:
    inventory = capability_inventory()
    text = (REPO_ROOT / "docs" / "scenarios.md").read_text(encoding="utf-8")
    for signature in inventory["task_signatures"]:
        assert signature in text
    for command in inventory["slash_commands"]:
        assert f"/{command}`" in text or f"`/{command}`" in text


def test_scenarios_markdown_mentions_learning_scenarios() -> None:
    inventory = capability_inventory()
    text = (REPO_ROOT / "docs" / "scenarios.md").read_text(encoding="utf-8")
    for item in inventory["learning_scenarios"]:
        assert item["name"] in text
