from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from rocky.capabilities import capability_inventory


def _markdown_for_inventory(data: dict) -> str:
    lines: list[str] = []
    lines.append("# Rocky capability catalog")
    lines.append("")
    lines.append(f"Version: {data['version']}")
    lines.append("")
    lines.append("## Route scenarios")
    lines.append("")
    lines.append("| Task signature | Lane | Class | Risk | Tool families | Preferred tools |")
    lines.append("|---|---|---|---|---|---|")
    for signature, profile in data["task_signatures"].items():
        lines.append(
            f"| `{signature}` | `{profile['lane']}` | `{profile['task_class']}` | `{profile['risk']}` | {', '.join(profile['tool_families']) or '—'} | {', '.join(profile['preferred_tools']) or '—'} |"
        )
    lines.append("")
    lines.append("## Operator commands")
    lines.append("")
    lines.append(", ".join(f"`/{name}`" for name in data["slash_commands"]))
    lines.append("")
    lines.append("## Built-in tools")
    lines.append("")
    lines.append(", ".join(f"`{name}`" for name in data["tool_names"]))
    lines.append("")
    lines.append("## Learning scenarios")
    lines.append("")
    lines.append("| Scenario | What it covers |")
    lines.append("|---|---|")
    for item in data["learning_scenarios"]:
        lines.append(f"| `{item['name']}` | {item['description']} |")
    lines.append("")
    lines.append("## Harness scenarios")
    lines.append("")
    lines.append("| Scenario | Phases |")
    lines.append("|---|---|")
    for item in data["harness"]["scenarios"]:
        lines.append(f"| `{item['name']}` | {', '.join(item['phases'])} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    docs_dir = REPO_ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)
    data = capability_inventory()
    (docs_dir / "capabilities.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (docs_dir / "scenarios.md").write_text(_markdown_for_inventory(data), encoding="utf-8")
    print("wrote docs/capabilities.json and docs/scenarios.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
