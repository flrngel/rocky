from __future__ import annotations

import pytest

from rocky.app import RockyRuntime
from rocky.tools import browser


def _tool_context(tmp_path):
    runtime = RockyRuntime.load_from(tmp_path)
    runtime.permissions.config.mode = "bypass"
    return runtime.tool_registry.context


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


def test_agent_browser_command_rejects_chained_subcommands() -> None:
    with pytest.raises(ValueError, match="exactly one browser subcommand"):
        browser._build_agent_browser_command("open https://example.com; snapshot -i --json")


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


def test_extract_browser_observations_preserves_json_error_without_fake_snapshot() -> None:
    observations, success = browser._extract_browser_observations(
        "snapshot -i --json",
        '{"success": false, "data": null, "error": "browserType.launch: Executable doesn\'t exist"}',
    )

    assert success is False
    assert observations["error"] == "browserType.launch: Executable doesn't exist"
    assert "snapshot" not in observations


def test_agent_browser_marks_runtime_unavailable_errors(tmp_path, monkeypatch) -> None:
    ctx = _tool_context(tmp_path)

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = (
            "browserType.launch: Executable doesn't exist at /tmp/browser\n"
            "Please run the following command to download new browsers:\n"
            "npx playwright install\n"
        )

    monkeypatch.setattr(browser.shutil, "which", lambda _name: "/opt/homebrew/bin/agent-browser")
    monkeypatch.setattr(browser.subprocess, "run", lambda *args, **kwargs: _Proc())

    result = browser.agent_browser(ctx, {"command": "open https://example.com"})

    assert result.success is False
    assert result.metadata["error"] == "browser_runtime_unavailable"
    assert "runtime is unavailable" in result.summary.lower()
