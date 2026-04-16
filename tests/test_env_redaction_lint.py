"""
O17 — Env-redaction lint invariant (P1).

Walks every module under ``src/rocky/tools/`` and asserts that any tool whose
``ToolResult`` payload carries a captured subprocess output (stdout/stderr) also
routes that output through :func:`rocky.util.redaction.redact_env_output`.

The test bites by:

1. **Sentinel unit** — a tiny ``lint_tool_source`` helper is applied to two
   synthetic module source strings: one non-compliant (stdout captured without
   redaction) must yield at least one violation; a compliant variant must yield
   none.
2. **Production-path walk** — the same helper is run across every real
   ``src/rocky/tools/*.py`` module; zero violations are expected today. This
   invariant fires when a future tool adds a captured-subprocess channel
   without also calling :func:`redact_env_output`.

Both halves are necessary: the sentinel unit proves the walker flags real
non-compliance, and the production walk proves it's wired to real files.

If the invariant fires on a future tool, either add the ``redact_env_output``
call or, if the tool does not actually capture subprocess output, extend
``EXEMPT_MODULES`` with a concrete rationale (not a bypass).
"""
from __future__ import annotations

import ast
import inspect
import pkgutil
from pathlib import Path

import pytest

import rocky.tools as tools_pkg

# Keywords that indicate the ToolResult payload carries captured subprocess
# output (stdout / stderr) or a raw external body. If any of these appears as
# a dict-value or ToolResult argument in a module, the module MUST call
# redact_env_output on its way there.
SUBPROCESS_CAPTURE_KEYS = ("stdout", "stderr")

# Modules in rocky.tools that are known not to capture subprocess output.
# Extending this list requires a one-line rationale above the entry. New tools
# that capture subprocess output MUST NOT be added here.
EXEMPT_MODULES: dict[str, str] = {
    "base": "defines Tool/ToolResult/ToolContext; no tool entry points",
    "registry": "assembles the registry; no subprocess capture",
    "__init__": "package init; no tool entry points",
    "proxy_support": "proxy construction helpers; no subprocess capture",
    "filesystem": "reads workspace files only; no subprocess stdout/stderr",
    "web": "fetches HTTP bodies; non-shell content path",
    "spreadsheet": "reads workbook cells; no subprocess capture",
}


def _module_source(mod_name: str) -> str:
    full = f"{tools_pkg.__name__}.{mod_name}"
    try:
        import importlib

        module = importlib.import_module(full)
    except Exception as exc:  # pragma: no cover - surfaced as skip in CI
        pytest.fail(f"Failed to import {full}: {exc!r}")
    return inspect.getsource(module)


def _has_subprocess_capture_key(src: str) -> bool:
    """True when module source references a subprocess-capture channel in a
    dict literal, kwarg, or similar assignment position.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        # dict literal: {'stdout': ...} or {"stderr": ...}
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    if k.value in SUBPROCESS_CAPTURE_KEYS:
                        return True
        # keyword argument: stdout=..., stderr=...
        if isinstance(node, ast.keyword):
            if node.arg in SUBPROCESS_CAPTURE_KEYS:
                return True
    return False


def _calls_redact_env_output(src: str) -> bool:
    return "redact_env_output" in src


def lint_tool_source(module_src: str, *, module_label: str = "<in-memory>") -> list[str]:
    """Return a list of violation messages for a single module source.

    A violation is: the source references a captured-subprocess channel
    (``'stdout'`` or ``'stderr'``) but does NOT call
    :func:`redact_env_output`. This heuristic is deliberately syntactic —
    an AST-level precision pass would be higher fidelity but this check is a
    lint gate, not a runtime enforcement.
    """
    violations: list[str] = []
    if _has_subprocess_capture_key(module_src) and not _calls_redact_env_output(module_src):
        violations.append(
            f"{module_label}: captures subprocess output (stdout/stderr) without redact_env_output"
        )
    return violations


# --------------------------------------------------------------------------
# 1. Sentinel unit — injected non-compliant source must trigger a violation;
#    compliant source must not.
# --------------------------------------------------------------------------


BAD_SOURCE = """
def fake_tool(ctx, args):
    proc = subprocess.run(['date'], capture_output=True, text=True)
    data = {'stdout': proc.stdout, 'stderr': proc.stderr}
    return ToolResult(True, data, 'ran', {})
"""


GOOD_SOURCE = """
from rocky.util.redaction import redact_env_output

def fake_tool(ctx, args):
    proc = subprocess.run(['date'], capture_output=True, text=True)
    data = {
        'stdout': redact_env_output(proc.stdout),
        'stderr': redact_env_output(proc.stderr),
    }
    return ToolResult(True, data, 'ran', {})
"""


def test_lint_flags_non_compliant_sentinel() -> None:
    violations = lint_tool_source(BAD_SOURCE, module_label="sentinel.bad")
    assert violations, (
        "lint_tool_source must flag a module that captures stdout/stderr "
        "without calling redact_env_output"
    )


def test_lint_clears_compliant_sentinel() -> None:
    violations = lint_tool_source(GOOD_SOURCE, module_label="sentinel.good")
    assert violations == [], (
        f"A compliant module must produce zero violations; got: {violations}"
    )


# --------------------------------------------------------------------------
# 2. Production walk — every real tool module must be compliant today.
# --------------------------------------------------------------------------


def _iter_tool_modules() -> list[tuple[str, str]]:
    """Yield (short_name, source) for every tool module in rocky.tools."""
    entries: list[tuple[str, str]] = []
    pkg_path = Path(tools_pkg.__file__).parent
    for info in pkgutil.iter_modules([str(pkg_path)]):
        short = info.name
        entries.append((short, _module_source(short)))
    return entries


def test_all_registered_tools_use_redact_env_output() -> None:
    """Any module that references captured subprocess output must route it
    through redact_env_output. Exempt modules are listed in EXEMPT_MODULES with
    a rationale; adding a new tool that captures subprocess output without
    redaction will fail this test."""
    violations: list[str] = []
    for short, src in _iter_tool_modules():
        if short in EXEMPT_MODULES:
            # Defensive: the exemption must be real. If an exempt module
            # starts capturing subprocess output, flag it so the exemption
            # gets revisited.
            if _has_subprocess_capture_key(src) and not _calls_redact_env_output(src):
                violations.append(
                    f"exempt module {short!r} now captures subprocess output "
                    f"without redact_env_output — revisit EXEMPT_MODULES"
                )
            continue
        violations.extend(lint_tool_source(src, module_label=short))

    assert violations == [], (
        "Tools that capture subprocess output must call redact_env_output. "
        f"Violations: {violations}"
    )
