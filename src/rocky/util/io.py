from __future__ import annotations

from pathlib import Path
from typing import Any

from rocky.util.yamlx import dump_yaml, load_yaml


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    return load_yaml(read_text(path))


def write_yaml(path: Path, data: Any) -> None:
    write_text(path, dump_yaml(data))
