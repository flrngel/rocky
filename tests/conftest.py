from __future__ import annotations

import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Allow xlfg artifact status annotations (e.g. `Status: DONE`) in test files.
builtins.DONE = "DONE"  # type: ignore[attr-defined]
builtins.BLOCKED = "BLOCKED"  # type: ignore[attr-defined]
builtins.FAILED = "FAILED"  # type: ignore[attr-defined]
