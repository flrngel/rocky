"""Drift guard — `.agent-testing/specs/*.json` required-key contract.

Every agent-testing spec under ``.agent-testing/specs/`` must carry the
minimum schema declared in the run-20260416-205534 follow-ups:
``name``, ``style``, ``target_path``, ``commands``. The check bites the
moment any current or future spec drops one of those keys.

Glob is ``*.json`` strictly — ``.agent-testing/specs/sl-promote.json.md``
is a companion markdown doc, not a spec; using ``*`` would spuriously
fail there.
"""

from __future__ import annotations

import json
from pathlib import Path


_REQUIRED_KEYS = {"name", "style", "target_path", "commands"}


def test_specs_have_required_keys() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    specs_dir = repo_root / ".agent-testing" / "specs"
    spec_files = sorted(specs_dir.glob("*.json"))
    assert spec_files, f"No *.json specs found under {specs_dir}"
    for spec_file in spec_files:
        data = json.loads(spec_file.read_text())
        missing = _REQUIRED_KEYS - data.keys()
        assert not missing, (
            f"{spec_file.name}: missing required keys {sorted(missing)} "
            f"(spec key contract is {sorted(_REQUIRED_KEYS)})"
        )
