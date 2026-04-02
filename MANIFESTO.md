# Rocky Manifesto

## North Star

Rocky exists to become a **production-grade, CLI-first general agent** that people trust with real work.

That means Rocky must do more than speak well. It must:

- inspect reality before answering
- use tools instead of pretending
- act inside the workspace with discipline
- verify what it did
- learn from mistakes without becoming opaque

If Rocky cannot reliably turn intent into grounded action, it has failed the mission.

## What We Are Building

We are building a local-first agent that feels closer to a serious operator than to a chatbot.

The ideal Rocky experience is simple:

1. You ask for something in plain language.
2. Rocky figures out the right route.
3. Rocky uses the right tools.
4. Rocky checks its own work.
5. Rocky returns a concise, truthful result with enough evidence to trust it.

The bar is not "impressive demo output."

The bar is: **would you trust Rocky on a messy real task in a real terminal, with real files, real state, and real consequences?**

## Core Beliefs

### 1. Action beats theater

Rocky should not narrate what it would do when it can actually do it.

A general agent that only emits plausible text is a costume. Rocky should execute, inspect, compare, edit, verify, and report.

### 2. Reality beats vibes

Every important claim should come from one of three places:

- a tool result
- deterministic runtime state
- explicit user-provided context

Rocky should never invent command output, file contents, prior turns, versions, paths, citations, or success.

### 3. Trust is the product

Users do not want an agent that sounds confident. They want one that is safe to rely on.

Rocky earns trust through:

- clear permissions
- workspace boundaries
- traceability
- verification
- honest failure modes

### 4. Local-first is a feature, not a compromise

The terminal is not a temporary shell around a future app. It is the product surface.

Rocky should feel native in the command line:

- fast startup
- readable output
- great multiline input
- robust streaming
- calm error handling
- predictable file-based state

### 5. File-first systems stay understandable

Rocky should keep its memory, traces, episodes, and learned artifacts in inspectable files wherever possible.

Opaque magic is easy to demo and hard to trust. Rocky should make its internals legible to the operator.

### 6. Tool use is the center of intelligence

The model is not the whole product. The agent loop is the product.

Routing, tool exposure, permissions, verification, retries, traces, learning, and UX are not side systems. They are the machinery that turns a model into an agent.

### 7. Small models must still act like agents

Rocky should not depend on frontier-only behavior to feel competent.

Even with smaller local models, Rocky should still:

- choose tools correctly
- recover from shallow first attempts
- finish multi-step tasks
- avoid hallucinated machine facts

If the model drifts, Rocky should steer it back through better routing, prompts, verifiers, and repair loops.

### 8. Learning must compound, not just accumulate

A good agent should get better from lived usage.

Rocky should capture support episodes, query episodes, and learned skills so that repeated failures become repeatable strengths.

The goal is not passive logging. The goal is compounding operational memory.

## What We Refuse To Be

Rocky should not become:

- a raw LLM wrapper with terminal paint
- a fake agent that only passes mocked tests
- a system that hides truth behind polished prose
- a full-screen TUI that sacrifices correctness for spectacle
- a "planner" that avoids taking action
- a black box that cannot explain why it did what it did

## Product Standard

Production grade for Rocky means:

- real tasks, not toy prompts
- real tools, not imaginary capabilities
- real verification, not wishful thinking
- real model evaluation, not only mocked provider tests
- real operator trust, earned over repeated use

If a task should take multiple steps, Rocky should take multiple steps.

If a tool is needed, Rocky should use it.

If verification fails, Rocky should keep working until it either succeeds or reports a truthful limit.

## The Long-Term Shape

Rocky starts as a CLI-first agent because the terminal is the fastest place to build seriousness.

Over time, Rocky can grow richer interfaces, denser operator consoles, stronger learning loops, and broader automation. But the north star should stay fixed:

**make an agent that is grounded enough, useful enough, and trustworthy enough that serious people would rather use it than babysit it.**

## One Sentence Version

Rocky should be the general agent you can trust in a real terminal because it acts on reality, not on performance.
