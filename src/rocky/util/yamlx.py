from __future__ import annotations

from typing import Any

import yaml


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def load_yaml(text: str) -> Any:
    return yaml.safe_load(text) if text.strip() else None


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    front = text[4:end]
    body = text[end + 5 :]
    data = yaml.safe_load(front) or {}
    if not isinstance(data, dict):
        data = {}
    return data, body
