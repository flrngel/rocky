# Rocky

**A CLI-first general agent that acts on reality, not on performance.**

Rocky is a local-first agent that lives in your terminal. It routes your task, picks the right tools, runs them against your real workspace, verifies what it did, and gets quietly better at your work over time. It is designed around one bar: **would you trust it on a messy real task with real files, real state, and real consequences?**

Default backend is local Ollama. No frontier model required.

```bash
rocky "find every TODO in src/, group by file, write the result to TODOS.md"
```

---

## Why Rocky exists

Most "agents" are LLMs in a costume — they describe what they *would* do, narrate plausible tool use, and pass mocked tests. Rocky started from the opposite premise:

> An agent's job is to **turn intent into grounded action**. If it can't do that on a small local model, in a real terminal, against real files — it has failed the mission.

This shapes every design choice:

- **Action beats theater** — Rocky executes, inspects, edits, and verifies. It does not narrate hypothetical work.
- **Reality beats vibes** — every load-bearing claim must come from a tool result, deterministic runtime state, or explicit user context. No invented file contents, command output, citations, or "success".
- **File-first, inspectable** — memory, learned policies, traces, episodes, and the learning ledger all live as readable files under `.rocky/`. Nothing important hides in a vector store.
- **Trust is the product** — clear permissions, workspace boundaries, traceable evidence, honest failure modes. "I don't know" is a valid answer.
- **Small models still have to act like agents** — Rocky steers smaller local models with better routing, prompts, verifiers, and repair loops rather than waiting for GPT-5.

The full philosophy lives in [`MANIFESTO.md`](MANIFESTO.md).

---

## Quick start

```bash
# Install (editable, dev extras)
uv pip install -e ".[dev]"

# One-shot
rocky "summarise what changed in the last 10 commits"

# Interactive REPL
rocky

# Override provider/model
rocky --provider ollama --model gpt-oss:20b "your task here"
```

First run launches a config wizard. Config lives at `~/.config/rocky/config.yaml`; per-workspace state lives in `.rocky/`.

Permission modes: `plan`, `supervised`, `accept-edits`, `auto`, `bypass`.

---

## How it works

A turn flows through four subsystems:

1. **Router** (`core/router.py`) — classifies the task into a `TaskClass` (REPO, RESEARCH, DATA, SITE, AUTOMATION, …) and a `Lane` (DIRECT / STANDARD / DEEP / META). Multi-turn continuations are detected lexically.
2. **Context builder** (`core/context.py`) — assembles a `ContextPackage`: system prompt, retrieved memories, matching skills, learned policies, evidence graph, and the answer contract the verifier will check.
3. **Agent core** (`core/agent.py`) — runs the turn loop. Sends messages to the provider, dispatches tool calls through the registry, accumulates evidence, and runs a flow-controlled multi-burst loop for any task that needs tools.
4. **Verifier** (`core/verifiers.py`) — checks evidence support, list/format requirements, and tool success before the answer is allowed to ship.

### Tools

Tool families are deliberately small and operator-grade:

- `shell` — sandboxed shell with cwd discipline and rc bootstrap
- `filesystem` — read / write / patch with permission gating
- `web` / `agent_browser` — DuckDuckGo + Brave search with query broadening, readability extraction, anti-bot detection, headless browser fallback
- `spreadsheet` — pandas / openpyxl
- `git`
- `python_exec`

Every tool returns structured `ToolResult`s; traces are saved to `.rocky/traces/` for inspection.

### Providers

`providers/registry.py` supports three styles: `LITELLM_CHAT` (default — local Ollama), `OPENAI_CHAT`, and `OPENAI_RESPONSES`. Provider, model, and base URL are overridable per-call from the CLI.

---

## The learning system

This is what makes Rocky different from a polished REPL.

Rocky has **two distinct kinds of guidance** and one **canonical ledger** that ties them together:

| | Authored skills | Learned policies / memories |
|---|---|---|
| Where | `data/bundled_skills/*/SKILL.md` + user skill roots | `.rocky/policies/learned/`, `.rocky/memories/`, `.rocky/student/` |
| Origin | Hand-written workflow files | Captured during real runs |
| Lifecycle | Versioned with the repo | Captured → candidate → promoted → (optionally) rolled back |

### Three ways Rocky gets better

1. **Teacher-driven correction (`/teach`, `/learn`)** — you correct a wrong answer once; Rocky synthesises a policy with declared task signatures, writes it to disk, and reuses it on a fresh process. Verified end-to-end, not just "policy file exists".
2. **Autonomous self-learning** — during normal `run_prompt` turns, Rocky auto-classifies preference/constraint statements into project memory (`SL-MEMORY`), persists self-retrospectives that cross process boundaries (`SL-RETROSPECT`), promotes candidate policies on first verified reuse (`SL-PROMOTE`), and rebuilds a project brief that survives restarts (`SL-BRIEF`).
3. **`/undo`** — every teach event creates one canonical lineage in the **Learning Ledger** (`.rocky/ledger/records.jsonl`). `/undo` rolls back the entire lineage atomically, including student notebook entries, patterns, policy directories, and reflection JSON. Unrelated lineages are guaranteed untouched (anti-monkey guard).

### The candidate-never-hard invariant

A learned rule must earn the right to constrain Rocky. Candidate policies are listed in the prompt for visibility but **never** emit hard `Do not:` / `Do:` lines until they reach `promotion_state == "promoted"`. This is enforced at two sites simultaneously (system prompt builder + agent constraint judge) so the two stay aligned.

### Honest about what doesn't yet work

The current ledger covers **write registration**. Retriever read paths still walk legacy filesystem stores; unifying them is Phase 2. Two known gaps are tracked as `xfail(strict=True)` tests rather than skips:

- **Derived-autonomous leak** — autonomous memories captured under a turn-lineage during a teach reuse are not moved by teach-lineage rollback. Phase 2 fix.
- **Retrospective style influence** — retrospectives load into context across restarts but on smaller models don't measurably reshape generation style. Phase-2 context-packer / stronger-model target.

Strict xfails mean the day Phase 2 lands, the tests flip to XPASS and force a status update. No silent wins.

Full deferred backlog: [`docs/xlfg/knowledge/hyperlearning-backlog.md`](docs/xlfg/knowledge/hyperlearning-backlog.md).

---

## Testing philosophy

From [`AGENTS.md`](AGENTS.md):

- Test Rocky through the **installed `rocky` CLI**, not only direct Python calls.
- Use the **real Ollama setup**. Mock providers do not prove agentic behavior.
- Grade route selection, real tool use from traces, final answer quality, produced files, and `/learn` persistence in a fresh process.
- Prefer generated workspaces over hard-coded fixtures.
- A scenario does not pass if Rocky skipped tools, returned an empty answer, or ignored the learned policy on retry.
- **Sensitivity checks** are mandatory: revert the fix, confirm the test fails, restore — honest proof the test bites.
- Anti-tamper gate: every self-learn scenario blanks the on-disk policy store and asserts the reuse observation flips to negative.

The suite is roughly **308 deterministic tests (~10s, zero LLM)** plus a live self-learn catalog gated by `ROCKY_LLM_SMOKE=1` that runs against the real `.venv/bin/rocky` binary against real Ollama.

```bash
pytest                              # fast deterministic
ROCKY_LLM_SMOKE=1 pytest tests/test_self_learn_live.py   # live, ~220s
```

---

## How the idea evolved

Rocky started as a CLI wrapper with a usable REPL (`v0.1.0`). The journey since then has been a steady tightening of one question — *is this real?*

- **v0.1 → v0.2** — added a 50-scenario agentic contract suite, hardened tool routing, made live-LLM phases the default test path. Workspace-scoped automatic memory landed.
- **Manifesto era** — formalised the "act on reality, not on performance" stance. Removed source-level case logic that just satisfied scenarios.
- **v0.3 → v1.0** — `/learn` rewritten as a reflective self-debugging loop that verifies feedback mismatches before publishing; learned policies separated from authored skills; evidence-first answering enforced at answer time; tool surface trimmed.
- **Web hardening** — query broadening, tighter bot detection, readability extraction, structured step traces, browser hint emission on bot challenge.
- **Flow loop** — widened from research/site to all tasks with tools; non-finalize early returns get full verification.
- **Hyperlearning v2 Phase 0** — candidate-never-hard invariant, `/policies` removed, `/learn` hidden behind a one-cycle alias, `/learned review` filters to candidates only.
- **Live self-learn (current)** — five autonomous scenarios proved end-to-end against real Ollama. The cheats from earlier runs (marker-injection, `/teach`-centric "self-learning") were replaced with scenarios that test what Rocky writes *autonomously during normal turns*.
- **Phase 1 canonical ledger (current)** — every `/teach` writes one `LearningRecord` with a lineage id; `/undo` moves all four artifact families atomically; second-order re-persistence bug closed by gating self-reflect on rollback state.

Two things have stayed fixed the whole way:

1. **Local-first is a feature, not a compromise.** The terminal is the product surface.
2. **Learning must compound, not just accumulate.** Repeated failures should become repeatable strengths — and rollback must be just as durable as capture.

---

## What Rocky refuses to be

- a raw LLM wrapper with terminal paint
- a fake agent that only passes mocked tests
- a system that hides truth behind polished prose
- a planner that avoids taking action
- a black box that cannot explain why it did what it did

---

## Repo layout

```
src/rocky/
  cli.py            # entrypoint
  app.py            # RockyRuntime.create() — wires every subsystem
  core/             # router, context, agent loop, verifiers, runtime state
  providers/        # litellm_local (default Ollama), openai_chat, openai_responses
  tools/            # shell, filesystem, web, browser, spreadsheet, git, python_exec
  learning/         # ledger, manager, episodes, policies, synthesis
  memory/           # store + retriever (file-first, auto-categorising)
  skills/           # loader + retriever for SKILL.md files
  ui/repl.py        # prompt_toolkit REPL with status bar
  data/bundled_skills/general-operator/
tests/              # 300+ deterministic + live self-learn catalog
docs/xlfg/          # current state, hyperlearning backlog, run evidence
```

---

## Status

Active development. v1.1.0. Phase 1 of Hyperlearning v2 shipped. Phases 2–4 (unified retrieval, bounded meta-learning archive, transfer evaluation) on the backlog.

License: MIT.
