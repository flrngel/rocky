# Rocky Manifesto

## One sentence

Rocky should be the general agent you can trust in a real terminal because it acts on reality, not on performance.

## North star

Rocky exists to become a **production-grade, CLI-first general agent** that people trust with real work.

The bar is not "impressive demo output." The bar is:

> Would you trust Rocky on a messy real task — in a real terminal, with real files, real state, and real consequences?

If the answer is no, nothing else we ship matters.

## What "real" means here

Three sources of truth, in this order:

1. A tool result.
2. Deterministic runtime state.
3. Explicit user-provided context.

That's it. Rocky never invents command output, file contents, prior turns, versions, paths, citations, or success. If a fact has no source, it doesn't get said. "I don't know" is a complete answer.

## Core beliefs

### 1. Action beats theater

A general agent that only emits plausible text is a costume. Rocky should execute, inspect, compare, edit, verify, and report. If a tool is needed, Rocky uses it. If a step is needed, Rocky takes it.

### 2. Reality beats vibes

Confidence is not the product. Groundedness is. A short answer with one cited tool result beats a long answer with none.

### 3. Trust is the product

Users don't want an agent that sounds smart. They want one that is safe to rely on. Trust comes from clear permissions, workspace boundaries, traceable evidence, calm error handling, and honest failure modes.

### 4. Local-first is a feature, not a compromise

The terminal is not a temporary shell around a future app. It is the product surface. Fast startup, readable output, robust streaming, predictable file-based state — all non-negotiable.

### 5. File-first systems stay legible

Memory, traces, episodes, learned policies, and the learning ledger live as inspectable files under `.rocky/`. Operators can `cat`, `grep`, and `git diff` Rocky's mind. Nothing important hides in an opaque store.

### 6. Tool use is the center of intelligence

The model is not the whole product. The agent loop is the product. Routing, tool exposure, permissions, verification, retries, traces, learning, and UX are not side systems — they are the machinery that turns a model into an agent.

### 7. Small models must still act like agents

Rocky cannot depend on frontier-only behavior to feel competent. On a small local Ollama model it must still pick the right tool, recover from a shallow first attempt, finish a multi-step task, and avoid hallucinated machine facts. When the model drifts, Rocky steers it back through better routing, prompts, verifiers, and repair loops — not by waiting for a bigger model.

### 8. Learning must compound, not just accumulate

Logging is not learning. Capture is not promotion. Rocky's learning system exists so that repeated failures become repeatable strengths — and so the operator can roll any of it back atomically when they change their mind.

## Honesty rules

These are operating rules, not aspirations.

- **Candidate-never-hard.** A learned rule must earn the right to constrain Rocky. Candidate policies stay visible but cannot emit hard `Do not:` / `Do:` lines until they're promoted by verified reuse.
- **One canonical lineage per teach.** Every `/teach` event writes one `LearningRecord`. `/undo` rolls back the entire lineage atomically. No orphaned student notebook entries, patterns, or memory shards left behind.
- **Sensitivity checks are mandatory.** A fix without a sensitivity check is a guess. Revert the fix, confirm the test fails, restore — every time.
- **Strict xfail, never skip.** Known gaps are tracked as `xfail(strict=True)` so the day they pass, the suite forces a status update. Skips let regressions hide.
- **Real CLI, real provider.** Rocky is tested through the installed `rocky` CLI against real Ollama. Mock providers do not prove agentic behavior. A scenario does not pass if Rocky skipped tools, returned an empty answer, or ignored the learned policy on retry.
- **Don't add source-level case logic to satisfy a scenario.** If a test passes only because the code knows about it, the test proves nothing.

## What Rocky refuses to be

- a raw LLM wrapper with terminal paint
- a fake agent that only passes mocked tests
- a system that hides truth behind polished prose
- a full-screen TUI that sacrifices correctness for spectacle
- a "planner" that avoids taking action
- a black box that cannot explain why it did what it did
- a learning system that captures forever and rolls back nothing

## Production standard

Production grade for Rocky means:

- real tasks, not toy prompts
- real tools, not imaginary capabilities
- real verification, not wishful thinking
- real evaluation against a real model, not only mocked provider tests
- real operator trust, earned over repeated use

If a task should take multiple steps, Rocky takes multiple steps. If verification fails, Rocky keeps working until it either succeeds or reports a truthful limit.

## The long shape

Rocky starts in the terminal because the terminal is the fastest place to build seriousness. Over time it can grow richer interfaces, denser operator consoles, stronger learning loops, and broader automation. The north star stays fixed:

**Make an agent that is grounded enough, useful enough, and trustworthy enough that serious people would rather use it than babysit it.**
