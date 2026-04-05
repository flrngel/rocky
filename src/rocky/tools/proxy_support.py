from __future__ import annotations

import os


TOOL_PROXY_ENV_VAR = "ROCKY_TOOL_PROXY"


def tool_proxy_url() -> str | None:
    value = os.environ.get(TOOL_PROXY_ENV_VAR, "").strip()
    return value or None
