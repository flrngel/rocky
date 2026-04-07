# Rocky v1.0.1

Rocky is a CLI-first, file-first, local-model-first general agent for real workspace tasks.

`v1.0.1` keeps the student-agent redesign from v1.0.0 and fixes the tool-execution path that was still too fragile with LiteLLM/Ollama and OpenAI-compatible harnesses:

- **LiteLLM-first provider stack** with Ollama routed through LiteLLM
- **continuable task threads** that do not disappear after a successful verification
- **persistent student notebook** for teacher feedback, domain knowledge, patterns, and examples
- **better TUI UX** with richer slash-command autocomplete, resume/new/status/freeze/student shortcuts, and state-rich toolbar hints
- **stronger continuation routing** so Rocky can actually keep working on the same task instead of resetting into generic chat

## v1.0.1 patch focus

| Area | v1.0.1 patch | Why it matters |
|---|---|---|
| Tool-call parsing | Normalizes `tool_calls`, deprecated `function_call`, dict arguments, and missing IDs | Rocky now executes tools against more real backend response shapes instead of silently stalling |
| Assistant/tool transcript | Preserves `content: null` on assistant tool-call messages and keeps `tool_call_id` wired through | Matches the message shapes expected by modern tool loops and avoids poisoning the conversation with literal `"None"` |
| Tool schema hygiene | Sanitizes JSON schema inputs and closes object schemas with `additionalProperties: false` by default | Makes tool contracts clearer and closer to Codex-style strict tool surfaces |
| Regression coverage | Added provider tests for LiteLLM/OpenAI-compatible edge cases | Prevents this exact class of harness regression from coming back |

## What Rocky is optimizing for

- useful local-model operation before hosted frontier-model assumptions
- practical repo, shell, automation, extraction, and data workflows
- inspectable state in `.rocky/` instead of opaque hidden services
- explicit traces, verifiers, and tuning surfaces
- future tuning by a coding agent, Claude Code, Codex, or a human without a rewrite
- a **teacher → student** operating model where Rocky can become more standalone over time

## What changed in v1.0.0

| Area | Change | Why it matters |
|---|---|---|
| Provider layer | Added `litellm_chat` and a new LiteLLM provider | More robust OpenAI-compatible/Ollama integration and less backend-specific harness fragility |
| Continuation | Successful verification now moves a thread to `awaiting_user` instead of `completed` | Follow-up prompts like “continue”, “what next”, and “finish it” keep the same task alive |
| Student memory | Added `.rocky/student/` with `profile.md`, `notebook.jsonl`, `knowledge/`, `patterns/`, `examples/` | Rocky can learn durable operator guidance beyond short chat context |
| Prompt assembly | Student notes and profile are injected into context | Teacher feedback can shape later runs without hard-coding behaviors |
| TUI | Better slash autocomplete, keyboard shortcuts, richer toolbar | Faster production use in long-running terminal sessions |
| Commands | Added `/teach`, `/student`, `/threads` | Easier inspection and correction loops |

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

Default config is created at `~/.config/rocky/config.yaml` with **LiteLLM local** active:

```yaml
active_provider: litellm_local
providers:
  litellm_local:
    style: litellm_chat
    base_url: http://localhost:4000
    api_key_env: LITELLM_API_KEY
    model: ollama_chat/qwen3.5:4b
    thinking: true
    reasoning_effort: medium
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
permissions:
  mode: supervised
```

Typical local stack:

1. Run Ollama locally.
2. Point LiteLLM at Ollama.
3. Point Rocky at LiteLLM.

That keeps Rocky using one provider contract while you still run Ollama underneath.

## New student-agent layout

Project state now includes a dedicated student notebook:

```text
.rocky/
  sessions/
  memories/
  skills/
  student/
    README.md
    profile.md
    notebook.jsonl
    knowledge/
    patterns/
    examples/
  episodes/
  policies/
  artifacts/
  traces/
  eval/
  cache/
```

## Core operator features

- interactive REPL (`rocky`)
- one-shot execution (`rocky "task"`)
- deterministic slash commands:
  - `/help`, `/tools`, `/skills`, `/harness`, `/memory`, `/student`, `/threads`, `/teach`, `/learned`, `/permissions`, `/context`, `/status`, `/sessions`, `/resume`, `/new`, `/config`, `/doctor`, `/why`, `/compact`, `/freeze`, `/plan`, `/learn`, `/undo`, `/init`, `/trace`
- richer TUI shortcuts:
  - `Ctrl-R` resume
  - `Ctrl-N` new session
  - `Ctrl-T` status
  - `Ctrl-F` freeze
  - `Ctrl-G` student status
- support for **OpenAI chat**, **OpenAI responses**, and **LiteLLM chat** styles
- typed tools for filesystem, shell, python, web, browser, spreadsheets, and git
- `SKILL.md` loading from bundled/global/project/learned scopes
- verifier hooks and `/why` traceability

## Teacher → student loop

1. Run Rocky on real tasks.
2. Correct it with `/teach <feedback>` when you want durable notebook guidance.
3. Use `/learn <feedback>` when you want both notebook feedback and a learned `SKILL.md`.
4. Rocky retrieves matching notes, patterns, examples, memories, and learned skills on later runs.
5. Over time Rocky becomes more standalone for your project style.

## Recommended docs

- `RELEASE_v1.0.1.md`
- `UPGRADE_REPORT_v1.0.1.md`
- `docs/TOOL_EXECUTION_NOTES_v1.0.1.md`
- `docs/STUDENT_AGENT.md`
- `docs/LITELLM_MIGRATION.md`
- `docs/RESEARCH_NOTES_2026-04-06.md`

## Notes

- Browser tools use Playwright. If browser binaries are missing, Rocky degrades cleanly and tells the operator to run `playwright install`.
- Web search is implemented as a best-effort DuckDuckGo/Brave HTML fallback.
- Browser and web tools honor `ROCKY_TOOL_PROXY` for explicit proxy routing.
- The slow learner still emits an inspectable heuristic report instead of weight updates.
- Live agentic tests skip automatically when the configured local provider is unreachable.
