Status: DONE
"""Shared test helpers for agent self-learning live scenarios.

Exports constants, regex patterns, and helper functions used by
``tests/agent/test_self_learn_live.py``. Extracted so future test
modules under ``tests/agent/`` can reuse the same harness surface
without duplication.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

__all__ = [
    "ROCKY_BIN",
    "DEFAULT_ROCKY_BIN",
    "SMOKE_FLAG",
    "EVIDENCE_ROOT",
    "SUBPROCESS_TIMEOUT_S",
    "SHELL_VERIFICATION_RE",
    "PNPM_CMD_RE",
    "NPM_INSTALL_RE",
    "_find_repo_root",
    "_run_rocky",
    "_run_rocky_until",
    "_install_evidence_finalizer",
    "_context_memories",
    "_context_student_notes",
]


# ---------------------------------------------------------------------------
# Repo root discovery
# ---------------------------------------------------------------------------


def _find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return start.parent.parent


_REPO_ROOT = _find_repo_root(Path(__file__).resolve())

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VENV_ROCKY = _REPO_ROOT / ".venv" / "bin" / "rocky"
DEFAULT_ROCKY_BIN = str(_VENV_ROCKY) if _VENV_ROCKY.exists() else "rocky"
ROCKY_BIN = os.environ.get("ROCKY_BIN", DEFAULT_ROCKY_BIN)

SMOKE_FLAG = "ROCKY_LLM_SMOKE"
EVIDENCE_ROOT = _REPO_ROOT / ".agent-testing" / "evidence"
SUBPROCESS_TIMEOUT_S = int(os.environ.get("ROCKY_LLM_SMOKE_TIMEOUT_S", "300"))

# SL-RETROSPECT T1's retrospective is titled "Python functional verification
# via shell one-liners" — the retrospective captured the *verification style*
# the agent used (running the function from the shell to prove correctness).
# The T2 prompt asks for a new function with type hints + verification, but
# does NOT prescribe HOW to verify. If the retrospective crossed the process
# boundary and actually shaped generation, T2 will verify using a shell
# invocation pattern (e.g., `python3 -c "..."`, `python divider.py`, or
# equivalent). This regex is chosen deliberately to match a style element that
# the T2 prompt does not mandate — so the assertion measures retrospective
# influence, not prompt compliance.
SHELL_VERIFICATION_RE = re.compile(
    r"python3?\s+(-c\s|\S+\.py)|>>>\s|(?:^|\n)\s*\$\s*python",
    re.IGNORECASE | re.MULTILINE,
)
PNPM_CMD_RE = re.compile(r"pnpm\s+(add|install)", re.IGNORECASE)
NPM_INSTALL_RE = re.compile(r"npm\s+install", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_rocky(
    workspace: Path,
    *task_args: str,
    label: str,
    captures: dict,
    timeout_s: int | None = None,
) -> dict:
    cmd = [ROCKY_BIN, "--cwd", str(workspace), "--json", *task_args]
    effective_timeout = timeout_s if timeout_s is not None else SUBPROCESS_TIMEOUT_S
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        captures[f"{label}__stdout"] = exc.stdout or ""
        captures[f"{label}__stderr"] = exc.stderr or ""
        pytest.fail(
            f"autonomous self-learn: `rocky` timed out at label={label} after "
            f"{effective_timeout}s; cmd={cmd}"
        )
    captures[f"{label}__cmd"] = cmd
    captures[f"{label}__returncode"] = proc.returncode
    captures[f"{label}__stdout"] = proc.stdout
    captures[f"{label}__stderr"] = proc.stderr
    if proc.returncode != 0:
        pytest.fail(
            f"autonomous self-learn: rocky exited {proc.returncode} at label={label}\n"
            f"cmd={cmd}\nstderr={proc.stderr[:2000]}\nstdout={proc.stdout[:2000]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"autonomous self-learn: non-JSON stdout at label={label}: {exc}\n"
            f"stdout={proc.stdout[:2000]}"
        )


def _run_rocky_until(
    workspace: Path,
    *task_args: str,
    label: str,
    captures: dict,
    predicate,
    predicate_reason: str,
    max_attempts: int = 3,
    timeout_s: int | None = None,
) -> dict:
    """Run `rocky` up to `max_attempts` times; return the first JSON whose
    output satisfies `predicate(payload) -> bool`.

    Bounded harness-level retry for the gemma4:26b answer-hedging flake
    documented in run-20260414-212042. Previous runs proved that:
      - rephrasing the teach prompt cannot stabilize the flake (two attempts,
        both made stability worse);
      - the flake root cause is model ANSWER hedging ("could be `npm install`
        OR `pnpm add`"), not teach classification.

    Independent retries are the correct harness-level workaround because
    each rocky subprocess is an independent sampling from gemma's output
    distribution. Bounded at 3 attempts so genuine regressions still surface
    (three consecutive failures on independent trials is a real regression
    signal, not a flake). Every attempt is logged to `captures` so evidence
    is complete.

    The ORIGINAL `_run_rocky` helper still pytest.fails immediately on
    non-zero exit OR non-JSON stdout (process-level errors are real
    regressions, never retried). This wrapper only retries on predicate
    failure — a narrowly-scoped condition.
    """
    attempts: list[dict] = []
    for attempt_num in range(1, max_attempts + 1):
        attempt_label = f"{label}__attempt_{attempt_num}"
        payload = _run_rocky(
            workspace,
            *task_args,
            label=attempt_label,
            captures=captures,
            timeout_s=timeout_s,
        )
        attempts.append(payload)
        try:
            satisfied = bool(predicate(payload))
        except Exception as exc:
            satisfied = False
            captures[f"{attempt_label}__predicate_error"] = repr(exc)
        captures[f"{attempt_label}__predicate_satisfied"] = satisfied
        if satisfied:
            captures[f"{label}__final_attempt_num"] = attempt_num
            return payload
    # All attempts exhausted.
    summary = "\n".join(
        f"  attempt {i+1}: data={(p.get('data') or {})!r}; text={str(p.get('text') or '')[:200]!r}"
        for i, p in enumerate(attempts)
    )
    pytest.fail(
        f"autonomous self-learn: predicate unsatisfied after {max_attempts} attempts at label={label}\n"
        f"predicate_reason={predicate_reason}\n"
        f"attempts:\n{summary}"
    )


def _install_evidence_finalizer(
    request, scenario: str, workspace: Path, captures: dict
) -> None:
    dest = EVIDENCE_ROOT / scenario
    dest.mkdir(parents=True, exist_ok=True)

    def _copy() -> None:
        try:
            for key, value in captures.items():
                target = dest / f"{key}.txt"
                if isinstance(value, (list, tuple)):
                    target.write_text(" ".join(str(x) for x in value), encoding="utf-8")
                elif isinstance(value, dict):
                    target.write_text(
                        json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                else:
                    target.write_text(str(value), encoding="utf-8")
            for rel in (
                ".rocky/policies/learned",
                ".rocky/artifacts/rollback",
                ".rocky/artifacts/self_reflections",
                ".rocky/artifacts/learning_reflections",
                ".rocky/student",
                ".rocky/memories",
            ):
                src = workspace / rel
                if not src.exists():
                    continue
                snap = dest / f"snapshot__{rel.replace('/', '__').lstrip('_')}"
                if snap.exists():
                    shutil.rmtree(snap, ignore_errors=True)
                shutil.copytree(src, snap, dirs_exist_ok=True)
            traces_root = workspace / ".rocky" / "traces"
            if traces_root.exists():
                traces_snap = dest / "traces_snapshot"
                traces_snap.mkdir(parents=True, exist_ok=True)
                for tr in sorted(traces_root.glob("*.json"))[-6:]:
                    shutil.copy2(tr, traces_snap / tr.name)
        except Exception as exc:  # pragma: no cover - evidence must not mask failures
            (dest / "evidence_copy_error.txt").write_text(
                f"evidence finalizer failed: {exc}\n", encoding="utf-8"
            )

    request.addfinalizer(_copy)


def _context_memories(response: dict) -> list[dict]:
    trace = response.get("trace") or {}
    context = trace.get("context") or {}
    return list(context.get("memories") or [])


def _context_student_notes(response: dict) -> list[dict]:
    trace = response.get("trace") or {}
    context = trace.get("context") or {}
    return list(context.get("student_notes") or [])
