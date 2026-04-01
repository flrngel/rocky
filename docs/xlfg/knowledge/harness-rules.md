# Harness Rules

- venv at `.venv/`, managed by `uv`
- Install: `uv pip install -e ".[dev]"`
- Run tests: `.venv/bin/pytest` (or `uv run pytest`)
- Default pytest opts: `-q` (from pyproject.toml)
- No lint/format tools configured yet
