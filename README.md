# rocky

Rocky is a CLI-first, file-first, local-model-first general agent for real workspace tasks.

v0.3.0 upgrades Rocky from a prompt-routed tool assistant into a more runtime-grounded agent with:

- active task threads for multi-turn continuation
- evidence/claim tracking with provenance
- answer contracts that constrain final responses to the current ask
- candidate-first memory and learned behavior promotion
- stronger verification around unsupported claims and answer drift
- generator/oracle harness assets for repeatable capability evaluation

## What Rocky is optimizing for

- useful local-model operation before hosted frontier-model assumptions
- practical repo, shell, automation, extraction, and data workflows
- inspectable state in `.rocky/` instead of opaque hidden services
- explicit traces, verifiers, and tuning surfaces
- future tuning by a coding agent or engineer without a rewrite

## v0.3.0 highlights

- **Thread-aware runtime**: short follow-ups can stay attached to an active artifact-backed workflow instead of collapsing into generic chat.
- **Evidence-first answering**: Rocky now accumulates provenance-bearing claims from prompts and tool results, then builds an answer contract before finalizing a response.
- **Safer memory**: project memory capture now prefers supported claims, explicit user corrections, and verified paths over answer prose.
- **Candidate-first learning**: `/learn` now binds to the best available workflow thread context and publishes learned behaviors as candidates that can later be promoted after verified reuse.
- **Better retrieval**: memory and learned skill retrieval now factor task/thread relevance, provenance, contradiction state, and verified reuse signals.
- **Improved debuggability**: traces now include continuation decisions, thread snapshots, answer contracts, and supported claim snapshots.

## High-level runtime architecture

```text
prompt
  -> continuation resolver
  -> thread-aware router
  -> active task thread + evidence graph update
  -> context assembly (instructions + durable memory + learned behaviors + handoffs + thread/evidence)
  -> provider/tool loop
  -> answer contract build
  -> structured verification
  -> memory/learning gating
  -> trace + session persistence
```

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
rocky --provider ollama --base-url http://localhost:11434/v1 "profile data.xlsx"
rocky --provider openai --model gpt-5.2 "explain this project"
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
    auto/
    candidates/
    project_brief.md
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

## Core operator features

- interactive REPL (`rocky`)
- one-shot execution (`rocky "task"`)
- deterministic slash commands:
  - `/help`, `/tools`, `/skills`, `/harness`, `/memory`, `/learned`, `/permissions`, `/context`, `/status`, `/sessions`, `/resume`, `/new`, `/config`, `/doctor`, `/why`, `/compact`, `/plan`, `/learn`, `/undo`, `/init`, `/trace`
- workspace discovery and file-first `.rocky/` state
- global + project + local config precedence
- configurable providers with **Ollama** default and **OpenAI** compatibility
- support for both **OpenAI chat-completions** and **Responses API** styles
- typed tools for filesystem, shell, python, web, browser, spreadsheets, and git
- `SKILL.md` loading from bundled/global/project/learned/compat directories
- support episodes, query episodes, learned skill generation, rollback, and a slow-learner report
- verifier hooks and `/why` traceability

## Learning loop

1. Run Rocky on a task.
2. Correct Rocky with `/learn <feedback>`.
3. Rocky writes a support episode and a candidate learned `SKILL.md`.
4. Similar later tasks can retrieve that learned behavior.
5. Verified successful reuse can promote a candidate skill.

## Tuning and handoff docs

For serious tuning work, start with:

- `UPGRADE_REPORT_v0.3.0.md`
- `ROCKY_TUNING_KNOWLEDGE.md`
- `RELEASE_v0.3.0.md`

## Notes

- Browser tools use Playwright. If browser binaries are missing, Rocky degrades cleanly and tells the operator to run `playwright install`.
- Web search is implemented as a best-effort DuckDuckGo HTML fallback.
- The slow learner still emits an inspectable heuristic report instead of weight updates.
- Live agentic tests skip automatically when the configured local provider is unreachable.
