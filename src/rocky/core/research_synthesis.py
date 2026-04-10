from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import unquote, urlparse

from rocky.core.runtime_state import prompt_requests_list_output, requested_minimum_list_items
from rocky.tool_events import tool_event_payload


LIVE_ITEM_TOOLS = {"fetch_url", "agent_browser", "browser_render_page", "extract_links"}

TEXT_MODEL_PROMPT_RE = re.compile(
    r"\b(?:text[- ]?(?:generation\s+)?models?|llms?|language\s+models?|text-generation)\b",
    flags=re.I,
)
PARAMETER_LIMIT_RE = re.compile(r"\b(?:under|below|less than)\s+(\d+(?:\.\d+)?)\s*b\b", flags=re.I)
PARAMETER_SIZE_RE = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b\b", flags=re.I)
UPDATED_RE = re.compile(r"\bUpdated\s+([^•|,]+(?:,\s*\d{4})?)", flags=re.I)

NON_TEXT_MODEL_MARKERS = (
    "any-to-any",
    "audio-text-to-text",
    "audio-to-audio",
    "audio-to-text",
    "automatic speech recognition",
    "feature extraction",
    "image-text-to-image",
    "image-text-to-text",
    "image-to-3d",
    "image-to-image",
    "image-to-text",
    "robotics",
    "sentence similarity",
    "speech",
    "text-to-3d",
    "text-to-audio",
    "text-to-image",
    "text-to-speech",
    "video-to-video",
    "vision",
)


@dataclass(slots=True)
class ResearchListCandidate:
    label: str
    url: str
    observed_text: str
    source_url: str
    order: int
    text_confidence: int
    score: int


def _parameter_limit_b(prompt: str) -> float | None:
    match = PARAMETER_LIMIT_RE.search(prompt)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parameter_sizes_b(text: str) -> list[float]:
    sizes: list[float] = []
    for match in PARAMETER_SIZE_RE.finditer(text):
        try:
            sizes.append(float(match.group(1)))
        except ValueError:
            continue
    return sizes


def _parameter_size_labels(text: str, *, limit: float | None) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for match in PARAMETER_SIZE_RE.finditer(text):
        label = match.group(0).upper().replace(" ", "")
        normalized = label.lower()
        if normalized in seen:
            continue
        try:
            size = float(match.group(1))
        except ValueError:
            continue
        if limit is not None and size >= limit:
            continue
        seen.add(normalized)
        labels.append(label)
        if len(labels) >= 2:
            break
    return labels


def _wants_text_models(prompt: str) -> bool:
    return bool(TEXT_MODEL_PROMPT_RE.search(prompt))


def _text_confidence(prompt: str, observed_text: str) -> int:
    if not _wants_text_models(prompt):
        return 1
    lowered = observed_text.lower()
    if "text generation" in lowered or "text-generation" in lowered:
        return 2
    if any(marker in lowered for marker in NON_TEXT_MODEL_MARKERS):
        return 0
    return 1


def _passes_parameter_filter(prompt: str, observed_text: str) -> bool:
    limit = _parameter_limit_b(prompt)
    if limit is None:
        return True
    sizes = _parameter_sizes_b(observed_text)
    if not sizes:
        return False
    return all(size < limit for size in sizes)


def _label_from_url(url: str, fallback: str) -> str:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    if parts:
        return parts[-1]
    return " ".join(fallback.split("•", 1)[0].split())[:80] or url


def _source_url_for_event(event: dict[str, Any], data: dict[str, Any]) -> str:
    arguments = event.get("arguments") or {}
    return str(arguments.get("url") or data.get("url") or data.get("final_url") or "").strip()


def _items_for_event(event: dict[str, Any], data: Any) -> list[dict[str, Any]]:
    name = str(event.get("name") or "")
    if name in {"fetch_url", "browser_render_page"} and isinstance(data, dict):
        return [item for item in list(data.get("link_items") or []) if isinstance(item, dict)]
    if name == "agent_browser" and isinstance(data, dict):
        return [item for item in list(data.get("items") or []) if isinstance(item, dict)]
    if name == "extract_links" and isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _candidate_score(prompt: str, source_url: str, observed_text: str, text_confidence: int) -> int:
    score = text_confidence * 100
    lowered_prompt = prompt.lower()
    lowered_source = source_url.lower()
    lowered_text = observed_text.lower()
    if any(term in lowered_prompt for term in ("trending", "right now", "current", "currently")):
        if "trending" in lowered_source or "sort=trending" in lowered_source:
            score += 30
    if _parameter_limit_b(prompt) is not None:
        score += 10
    if "updated" in lowered_text:
        score += 5
    return score


def _observed_research_candidates(prompt: str, tool_events: list[dict[str, Any]]) -> list[ResearchListCandidate]:
    candidates: list[ResearchListCandidate] = []
    seen_urls: set[str] = set()
    order = 0
    for event in tool_events:
        if event.get("type") != "tool_result" or not event.get("success", True):
            continue
        name = str(event.get("name") or "")
        if name not in LIVE_ITEM_TOOLS:
            continue
        payload = tool_event_payload(event, exact=True)
        data = payload.get("data")
        data_dict = data if isinstance(data, dict) else {}
        source_url = _source_url_for_event(event, data_dict)
        for item in _items_for_event(event, data):
            url = str(item.get("url") or "").strip().rstrip(").,;:!?]")
            observed_text = " ".join(str(item.get("text") or item.get("title") or item.get("name") or "").split()).strip()
            if not url or not observed_text or url in seen_urls:
                continue
            text_confidence = _text_confidence(prompt, observed_text)
            if text_confidence <= 0:
                continue
            if not _passes_parameter_filter(prompt, f"{observed_text} {url}"):
                continue
            label = _label_from_url(url, observed_text)
            score = _candidate_score(prompt, source_url, observed_text, text_confidence)
            candidates.append(
                ResearchListCandidate(
                    label=label,
                    url=url,
                    observed_text=observed_text,
                    source_url=source_url,
                    order=order,
                    text_confidence=text_confidence,
                    score=score,
                )
            )
            seen_urls.add(url)
            order += 1
    return candidates


def _detail_phrase(candidate: ResearchListCandidate, prompt: str) -> str:
    details: list[str] = []
    observed_lower = candidate.observed_text.lower()
    if "text generation" in observed_lower or "text-generation" in observed_lower:
        details.append("Text Generation")
    size_labels = _parameter_size_labels(candidate.observed_text, limit=_parameter_limit_b(prompt))
    if size_labels:
        details.append("/".join(size_labels))
    if update_match := UPDATED_RE.search(candidate.observed_text):
        details.append("updated " + " ".join(update_match.group(1).split()))
    if not details:
        details.append(candidate.observed_text.split("•", 1)[0].strip())
    return "; ".join(details)


def build_counted_research_list_answer(
    prompt: str,
    route_task_signature: str,
    tool_events: list[dict[str, Any]],
) -> str:
    minimum_items = requested_minimum_list_items(prompt)
    if minimum_items <= 0 or not prompt_requests_list_output(prompt):
        return ""
    if not route_task_signature.startswith(("research/", "site/")):
        return ""

    candidates = _observed_research_candidates(prompt, tool_events)
    if _wants_text_models(prompt):
        explicit_text = [candidate for candidate in candidates if candidate.text_confidence >= 2]
        if len(explicit_text) >= minimum_items:
            candidates = explicit_text
    candidates = sorted(candidates, key=lambda item: (-item.score, item.order, item.url))
    if len(candidates) < minimum_items:
        return ""

    lines: list[str] = []
    for index, candidate in enumerate(candidates[:minimum_items], start=1):
        details = _detail_phrase(candidate, prompt)
        lines.append(f"{index}. [{candidate.label}]({candidate.url}) - {details}.")
    return "\n".join(lines)
