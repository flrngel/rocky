from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


def to_plain_data(data: Any) -> Any:
    if isinstance(data, Enum):
        return data.value
    if isinstance(data, Path):
        return str(data)
    if is_dataclass(data):
        return to_plain_data(asdict(data))
    if isinstance(data, dict):
        return {str(key): to_plain_data(value) for key, value in data.items()}
    if isinstance(data, (list, tuple, set)):
        return [to_plain_data(item) for item in data]
    return data


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(to_plain_data(data), sort_keys=False, allow_unicode=True)


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
