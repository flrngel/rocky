from __future__ import annotations

"""Machine-readable inventory of Rocky's built-in scenarios and surfaces."""

from dataclasses import MISSING
from typing import Any

from rocky.commands.registry import CommandRegistry
from rocky.core.router import Lane, Router, TaskClass
from rocky.harness.scenarios import harness_inventory
from rocky.tools.registry import (
    ALL_TOOL_NAMES,
    READ_ONLY_TASK_SIGNATURES,
    READ_ONLY_TOOL_NAMES,
    TASK_TOOL_PRIORITY,
)
from rocky.version import __version__


def _default_field_value(dataclass_type: type, field_name: str):
    field = dataclass_type.__dataclass_fields__[field_name]
    if field.default_factory is not MISSING:
        return field.default_factory()
    return field.default


def capability_inventory() -> dict[str, Any]:
    route_profiles: dict[str, dict[str, Any]] = {}
    for signature, profile in sorted(Router.TASK_SIGNATURE_PROFILES.items()):
        lane = profile["lane"]
        task_class = profile["task_class"]
        route_profiles[signature] = {
            "lane": lane.value if isinstance(lane, Lane) else str(lane),
            "task_class": task_class.value if isinstance(task_class, TaskClass) else str(task_class),
            "risk": str(profile["risk"]),
            "tool_families": list(profile.get("tool_families") or []),
            "preferred_tools": list(TASK_TOOL_PRIORITY.get(signature, [])),
            "read_only": signature in READ_ONLY_TASK_SIGNATURES,
        }

    harness = harness_inventory()
    harness_scenarios = []
    for item in harness.get("scenarios", []):
        harness_scenarios.append(
            {
                "name": str(item.get("name") or item.get("id") or "unnamed_scenario"),
                "phases": list(item.get("phases") or ["single_phase"]),
                "notes": str(item.get("notes") or ""),
            }
        )

    return {
        "version": __version__,
        "lanes": [lane.value for lane in Lane],
        "task_classes": [task_class.value for task_class in TaskClass],
        "task_signatures": route_profiles,
        "slash_commands": list(_default_field_value(CommandRegistry, "names")),
        "command_aliases": dict(_default_field_value(CommandRegistry, "aliases")),
        "tool_names": sorted(ALL_TOOL_NAMES),
        "read_only_tools": sorted(READ_ONLY_TOOL_NAMES),
        "learning_scenarios": [
            {
                "name": "SL-MEMORY",
                "description": "Autonomous capture and reuse of project memory from normal prompts.",
            },
            {
                "name": "SL-RETROSPECT",
                "description": "Autonomous self-reflection persisted across processes.",
            },
            {
                "name": "SL-PROMOTE",
                "description": "Candidate learned policy promotion after verified reuse.",
            },
            {
                "name": "SL-BRIEF",
                "description": "Automatic project-brief rebuilding from promoted memories.",
            },
            {
                "name": "UNDO",
                "description": "Atomic rollback of learning lineages via the ledger.",
            },
            {
                "name": "META-VARIANT",
                "description": "Safe runtime overlay experimentation with canarying and rollback.",
            },
        ],
        "harness": {
            "strategy": harness.get("strategy"),
            "provider": harness.get("provider"),
            "notes": list(harness.get("notes") or []),
            "phases": list(harness.get("phases") or []),
            "scenarios": harness_scenarios,
        },
    }


__all__ = ["capability_inventory"]
