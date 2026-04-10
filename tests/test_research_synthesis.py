from __future__ import annotations

import json

from rocky.core.research_synthesis import build_counted_research_list_answer


def _fetch_event(url: str, items: list[dict[str, str]]) -> dict:
    payload = {
        "success": True,
        "summary": f"Fetched {url}",
        "data": {
            "url": url,
            "title": "Example models",
            "text_excerpt": "Example listing",
            "link_items": items,
        },
        "metadata": {},
    }
    return {
        "type": "tool_result",
        "name": "fetch_url",
        "arguments": {"url": url},
        "success": True,
        "raw_text": json.dumps(payload),
        "model_text": "Fetched example listing",
        "summary_text": f"Fetched {url}",
        "facts": [],
        "artifacts": [{"kind": "url", "ref": url, "source": "fetch_url"}],
        "text": "Fetched example listing",
    }


def test_counted_research_synthesis_filters_observed_items_from_prompt_constraints() -> None:
    answer = build_counted_research_list_answer(
        "find text models under 12B parameters that are trending right now. show at least 3 as a list.",
        "research/live_compare/general",
        [
            _fetch_event(
                "https://example.test/models?sort=trending&search=8B",
                [
                    {
                        "text": "org/audio-8b Text-to-Speech • 8B • Updated 1 day ago",
                        "url": "https://example.test/org/audio-8b",
                    },
                    {
                        "text": "org/big-13b Text Generation • 13B • Updated 1 day ago",
                        "url": "https://example.test/org/big-13b",
                    },
                    {
                        "text": "org/alpha-8b Text Generation • 8B • Updated 1 day ago",
                        "url": "https://example.test/org/alpha-8b",
                    },
                    {
                        "text": "org/beta-7b Text Generation • 7B • Updated 2 days ago",
                        "url": "https://example.test/org/beta-7b",
                    },
                    {
                        "text": "org/gamma-1b Text Generation • 1B • Updated 3 days ago",
                        "url": "https://example.test/org/gamma-1b",
                    },
                ],
            )
        ],
    )

    assert answer.count("\n") == 2
    assert "org/alpha-8b" in answer
    assert "org/beta-7b" in answer
    assert "org/gamma-1b" in answer
    assert "org/audio-8b" not in answer
    assert "org/big-13b" not in answer
    assert "Text Generation" in answer
