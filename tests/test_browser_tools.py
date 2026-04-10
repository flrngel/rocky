from __future__ import annotations

from rocky.tools import browser


def test_agent_browser_command_defaults_to_headless_sessionless_invocation() -> None:
    assert browser._build_agent_browser_command("open https://example.com") == [
        "agent-browser",
        "open",
        "https://example.com",
    ]


def test_agent_browser_command_supports_session_and_headed() -> None:
    assert browser._build_agent_browser_command(
        "snapshot -i --json",
        session="research",
        headed=True,
    ) == [
        "agent-browser",
        "--session",
        "research",
        "--headed",
        "snapshot",
        "-i",
        "--json",
    ]


def test_extract_browser_observations_reads_snapshot_refs() -> None:
    observations, success = browser._extract_browser_observations(
        "snapshot -i --json",
        '{"success": true, "data": {"url": "https://huggingface.co/models", "title": "Trending Models", "snapshot": "- link \\"meta-llama/Llama-3.2-3B\\" [ref=e1]", "refs": {"e1": {"name": "meta-llama/Llama-3.2-3B", "role": "link"}}}}',
    )

    assert success is True
    assert observations["url"] == "https://huggingface.co/models"
    assert observations["title"] == "Trending Models"
    assert observations["items"] == [{"ref": "e1", "name": "meta-llama/Llama-3.2-3B", "role": "link"}]


def test_extract_browser_observations_uses_open_command_url_when_stdout_is_plain() -> None:
    observations, success = browser._extract_browser_observations(
        "open https://example.com",
        "",
    )

    assert success is True
    assert observations["url"] == "https://example.com"
