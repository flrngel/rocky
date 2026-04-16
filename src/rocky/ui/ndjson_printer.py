"""Stable machine-readable event stream format (NDJSON / JSON Lines).

O6 extras: every emitted event carries three envelope fields so downstream
parsers have ordering, timing, and versioning without guessing:

- ``seq`` — monotonic per-printer counter starting at 1 (resets each run).
- ``ts`` — ISO-8601 UTC timestamp of when the event was serialized.
- ``schema_version`` — top-level envelope contract version string.

The fields are added as a shallow copy (original event dict is not mutated),
so existing consumers that ignore unknown fields are unaffected.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


# Envelope contract version. Bump when a breaking change is introduced (fields
# removed, semantics changed). Additive fields do not require a bump.
NDJSON_SCHEMA_VERSION = "1.0"


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
        # O6: monotonic sequence counter scoped to this printer instance.
        # Starts at 1 (first emitted event has seq=1). Determinism within a
        # run; logs of multiple runs are distinguished by run id, not seq.
        self._seq: int = 0

    def _envelope(self, event: dict) -> dict:
        """Return a shallow copy of *event* with ``seq``/``ts``/``schema_version``
        injected. Does not mutate the caller's dict."""
        self._seq += 1
        enveloped = dict(event)
        enveloped["seq"] = self._seq
        enveloped["ts"] = datetime.now(timezone.utc).isoformat()
        enveloped["schema_version"] = NDJSON_SCHEMA_VERSION
        return enveloped

    def __call__(self, event: Any) -> None:
        """Serialize *event* as a single JSON line."""
        try:
            if isinstance(event, dict):
                kind = event.get("type", "")
                if kind == "assistant_chunk":
                    self.streamed_text = True
                enveloped = self._envelope(event)
                line = json.dumps(enveloped, default=str, ensure_ascii=False)
            else:
                try:
                    payload: Any = event.__dict__ if hasattr(event, "__dict__") else str(event)
                    if isinstance(payload, dict):
                        enveloped = self._envelope(payload)
                        line = json.dumps(enveloped, default=str, ensure_ascii=False)
                    else:
                        enveloped = self._envelope({"type": "raw", "value": str(payload)})
                        line = json.dumps(enveloped, default=str, ensure_ascii=False)
                except Exception:
                    enveloped = self._envelope({"type": "raw", "value": str(event)})
                    line = json.dumps(enveloped, default=str, ensure_ascii=False)
        except Exception as exc:
            # Error path also carries envelope so stream monotonicity holds.
            fallback = self._envelope({"type": "error", "message": f"ndjson serialize failed: {exc}"})
            line = json.dumps(fallback, ensure_ascii=False)
        self._stream.write(line + "\n")
        self._stream.flush()

    # alias so callers can use either style
    def handle(self, event: Any) -> None:
        self(event)

    def finish(self) -> None:
        """No-op; satisfies the EventPrinter finish() contract."""
