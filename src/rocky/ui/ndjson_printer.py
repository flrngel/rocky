"""Stable machine-readable event stream format (NDJSON / JSON Lines)."""
from __future__ import annotations

import json
import sys
from typing import Any


class NdjsonEventPrinter:
    """Emit one JSON object per line to a stream (default stdout).

    Compatible with the existing event_handler contract used by EventPrinter:
    - callable (implements __call__)
    - has a ``finish()`` no-op method
    - has a ``streamed_text`` bool attribute
    """

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self.streamed_text: bool = False

    def __call__(self, event: Any) -> None:
        """Serialize *event* as a single JSON line."""
        try:
            if isinstance(event, dict):
                kind = event.get("type", "")
                if kind == "assistant_chunk":
                    self.streamed_text = True
                line = json.dumps(event, default=str, ensure_ascii=False)
            else:
                try:
                    payload: Any = event.__dict__ if hasattr(event, "__dict__") else str(event)
                    if isinstance(payload, dict):
                        line = json.dumps(payload, default=str, ensure_ascii=False)
                    else:
                        line = json.dumps({"type": "raw", "value": str(payload)}, default=str, ensure_ascii=False)
                except Exception:
                    line = json.dumps({"type": "raw", "value": str(event)}, default=str, ensure_ascii=False)
        except Exception as exc:
            line = json.dumps({"type": "error", "message": f"ndjson serialize failed: {exc}"}, ensure_ascii=False)
        self._stream.write(line + "\n")
        self._stream.flush()

    # alias so callers can use either style
    def handle(self, event: Any) -> None:
        self(event)

    def finish(self) -> None:
        """No-op; satisfies the EventPrinter finish() contract."""
