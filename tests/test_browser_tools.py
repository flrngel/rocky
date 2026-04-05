from __future__ import annotations

from rocky.tools import browser
from rocky.tools.proxy_support import TOOL_PROXY_ENV_VAR


def test_browser_launch_options_default_to_headless(monkeypatch) -> None:
    monkeypatch.delenv(TOOL_PROXY_ENV_VAR, raising=False)

    assert browser._browser_launch_options() == {"headless": True}


def test_browser_launch_options_include_explicit_tool_proxy(monkeypatch) -> None:
    monkeypatch.setenv(TOOL_PROXY_ENV_VAR, "http://proxy.internal:8080")

    assert browser._browser_launch_options() == {
        "headless": True,
        "proxy": {"server": "http://proxy.internal:8080"},
    }
