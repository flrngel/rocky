from __future__ import annotations

import re


SENSITIVE_KEY_PATTERN = re.compile(
    r'(?:^|(?<=[\s"]))([A-Z_]*(?:TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL|AUTH|ACCESS_KEY)\w*)=([^\s"\\]+)',
    re.MULTILINE,
)

BLOCKED_VERIFICATION_COMMANDS: frozenset[str] = frozenset({"env"})


def redact_env_output(text: str) -> str:
    """Replace values of sensitive environment variable lines with <redacted>."""
    return SENSITIVE_KEY_PATTERN.sub(lambda m: f"{m.group(1)}=<redacted>", text)
