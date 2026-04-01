# Current State

## REPL (src/rocky/ui/repl.py)
- prompt_toolkit PromptSession with `multiline=True` and custom KeyBindings
- Enter submits, Alt+Enter (Escape+Enter) inserts newline
- Permission prompt (`ask_permission`) uses `multiline=False` override so Enter submits directly
- History: FileHistory at `{cache_dir}/repl_history.txt`
- Completion: WordCompleter for slash commands

## Test Harness
- venv: `.venv/` (managed by uv)
- Run tests: `.venv/bin/pytest` or `uv run pytest`
- Test files: tests/ (pytest with `-q` default from pyproject.toml)

## Open follow-ups
- P1: Add Alt+Enter discoverability hint to startup banner or /help output
