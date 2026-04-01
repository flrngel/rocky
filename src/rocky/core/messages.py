from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Message:
    role: str
    content: Any
    name: str | None = None
    tool_call_id: str | None = None
