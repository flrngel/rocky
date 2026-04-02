from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(slots=True)
class ProviderResponse:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    tool_events: list[dict[str, Any]] = field(default_factory=list)


TOOL_CITATION_RE = re.compile(r"\s*【[^】]+†[^】]+】")


def sanitize_assistant_text(text: str, *, strip: bool = True) -> str:
    cleaned = TOOL_CITATION_RE.sub("", text)
    return cleaned.strip() if strip else cleaned
