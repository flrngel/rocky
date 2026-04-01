# rocky

Rocky is a CLI-first, file-first, continuously learning general agent built from scratch against the Rocky PRD.

It is designed around five constraints:

- interactive by default
- one-shot when a task string is passed
- typed tools + `SKILL.md` skills + deterministic slash-command inspection
- file-first `.rocky/` state instead of a primary database
- real cross-task learning through support/query episodes and learned skill publication

## TUI choice

Rocky ships today with **prompt_toolkit + Rich** for the interaction layer because that combination gives the best Python-native CLI editing UX, stable history/completion, and rich rendering without forcing a full-screen app for every use case.

We also document a future-ready path to **Textual** and **OpenTUI** in `docs/TUI_RESEARCH.md`.

## What is implemented

- interactive REPL (`rocky`)
- one-shot execution (`rocky "task"`)
- deterministic slash commands:
  - `/help`, `/tools`, `/skills`, `/memory`, `/learned`, `/permissions`, `/context`, `/status`, `/sessions`, `/resume`, `/new`, `/config`, `/doctor`, `/why`, `/compact`, `/plan`, `/learn`, `/undo`, `/init`, `/trace`
- workspace discovery and file-first `.rocky/` state
- global + project + local config precedence
- configurable providers with **Ollama** default and **OpenAI** compatibility
- support for both **OpenAI chat-completions** and **Responses API** styles
- typed tools for filesystem, shell, python, web, browser, spreadsheets, and git
- `SKILL.md` loading from bundled/global/project/learned/compat directories
- support episodes, query episodes, learned skill generation, rollback, and a slow-learner report
- verifier hooks and `/why` traceability

## Quick start

```bash
pip install -e .
rocky
```

One-shot mode:

```bash
rocky "summarize the repo structure and list risky files"
```

Initialize project scaffold:

```bash
rocky init
```

## Provider configuration

Default config is created at `~/.config/rocky/config.yaml` with Ollama active:

```yaml
active_provider: ollama
providers:
  ollama:
    style: openai_chat
    base_url: http://localhost:11434/v1
    api_key_env: OLLAMA_API_KEY
    model: llama3.2
  openai:
    style: openai_responses
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    model: gpt-5.2
    store: false
permissions:
  mode: supervised
```

Override at runtime:

```bash
rocky --provider openai --model gpt-5.2 "explain this project"
rocky --provider ollama --base-url http://localhost:11434/v1 "profile data.xlsx"
```

## Layout

Global:

```text
~/.config/rocky/
  config.yaml
  AGENTS.md
  skills/
  memories/
  providers/
  policies/
  caches/
```

Project:

```text
.rocky/
  config.yaml
  config.local.yaml
  sessions/
  memories/
  skills/
    bundled/
    project/
    learned/
  episodes/
    support/
    query/
  policies/
  artifacts/
  traces/
  eval/
  cache/
```

## Learning loop

1. Run Rocky on a task.
2. Correct Rocky with `/learn <feedback>`.
3. Rocky writes a support episode and a learned `SKILL.md`.
4. The next analogous task retrieves the learned skill automatically.
5. Rocky writes query episodes with `skill_generation_seen` for support/query hygiene.

## Notes

- Browser tools use Playwright. If browser binaries are missing, Rocky degrades cleanly and tells the operator to run `playwright install`.
- Web search is implemented as a best-effort DuckDuckGo HTML fallback.
- The slow learner currently emits an inspectable heuristic report instead of performing weight updates.
