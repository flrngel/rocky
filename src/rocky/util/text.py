from __future__ import annotations

import hashlib
import json
from typing import Any


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - 32)
    return text[:keep] + f"\n... [truncated {len(text) - keep} chars]"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
