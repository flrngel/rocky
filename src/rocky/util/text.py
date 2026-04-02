from __future__ import annotations

import hashlib
import json
import re
from typing import Any


STOP_WORDS = {
    "a",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "for",
    "from",
    "get",
    "give",
    "help",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "just",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "show",
    "so",
    "tell",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "this",
    "to",
    "up",
    "use",
    "using",
    "want",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
    "your",
}


def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    keep = max(0, limit - 32)
    return text[:keep] + f"\n... [truncated {len(text) - keep} chars]"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def extract_json_candidate(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    candidates = [stripped]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.I | re.S)
    candidates.extend(item.strip() for item in fenced if item.strip())
    for candidate in candidates:
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    return None


def tokenize_keywords(text: str) -> set[str]:
    tokens: set[str] = set()
    for word in re.findall(r"[a-zA-Z0-9_:+./-]+", text.lower()):
        if len(word) <= 2 or word in STOP_WORDS:
            continue
        tokens.add(word)
        if word.endswith("s") and len(word) > 4:
            tokens.add(word[:-1])
    return tokens
