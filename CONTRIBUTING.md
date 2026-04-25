# Contributing to Rocky

Rocky changes should preserve the repo's trust-first contract: real tool use, inspectable state, honest verification, and reversible learning.

## Local setup

```bash
uv pip install -e ".[dev]"
```

## Deterministic test gate

```bash
pytest -q
```

## Live-LLM smoke gate

```bash
ROCKY_LLM_SMOKE=1 ROCKY_BIN=./.venv/bin/rocky pytest tests/agent/test_self_learn_live.py -v
```

## Release checklist

1. Run `python scripts/export_capabilities.py`.
2. Update `src/rocky/version.py` and `pyproject.toml` together, or use `python scripts/bump_version.py X.Y.Z`.
3. Update `CHANGELOG.md` and add `docs/releases/vX.Y.Z.md`.
4. Run `pytest -q`.
5. Keep `.rocky/` and `.agent-testing/{runs,evidence}/` out of commits.
