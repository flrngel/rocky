from __future__ import annotations

"""Public Rocky package surface.

Keep the root import lightweight: version reads should not eagerly import the
full agent runtime. Heavy public helpers are resolved lazily via ``__getattr__``.
"""

from rocky.version import __version__

__all__ = [
    "__version__",
    "ANSWER_CLOSE_MARKER",
    "ANSWER_OPEN_MARKER",
    "strip_markers",
]


def __getattr__(name: str):
    if name in {"ANSWER_CLOSE_MARKER", "ANSWER_OPEN_MARKER", "strip_markers"}:
        from rocky.core.agent import (
            ANSWER_CLOSE_MARKER,
            ANSWER_OPEN_MARKER,
            strip_markers,
        )

        exports = {
            "ANSWER_CLOSE_MARKER": ANSWER_CLOSE_MARKER,
            "ANSWER_OPEN_MARKER": ANSWER_OPEN_MARKER,
            "strip_markers": strip_markers,
        }
        return exports[name]
    raise AttributeError(f"module 'rocky' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
