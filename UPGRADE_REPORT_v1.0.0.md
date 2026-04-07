# Rocky v1.0.0 upgrade report

## Goal

Revise Rocky into a stronger **harness-compatible, learnable, standalone-leaning student agent**.

The requested target was not just “better answers.” It was:

1. keep working without stalling mid-task
2. work better with the harness
3. be teachable by a human/Codex/Claude Code
4. support richer terminal UX for production use
5. move toward a student agent that becomes more standalone over time

## Primary root cause addressed

The most important runtime issue fixed in this upgrade was continuation collapse.

### Previous failure mode

A successful verification immediately marked the active task thread as `completed`.
That sounds reasonable at first, but it weakens the next-turn continuation path.
When the operator says things like “continue”, “what next”, or “finish it”, the runtime has already removed the most relevant thread from the active continuation set.

### New behavior

- `pass` -> `awaiting_user`
- `fail` -> `needs_repair`
- otherwise -> `active`

This keeps verified work **continuable** instead of prematurely terminal.

## Detailed implementation map

| File | Change | Why it matters |
|---|---|---|
| `src/rocky/config/models.py` | Added `ProviderStyle.LITELLM_CHAT`, `reasoning_effort`, `tool_choice`, `extra_body`, default `litellm_local` provider | Rocky can speak through LiteLLM while still targeting Ollama underneath |
| `src/rocky/config/loader.py` | Added default LiteLLM config and parsing for new provider fields | Makes the LiteLLM path the normal path instead of a custom patch |
| `src/rocky/config/wizard.py` | Added LiteLLM as a top-level provider choice and `litellm_chat` compatible mode | Operator setup is now aligned with the preferred deployment shape |
| `src/rocky/providers/litellm_chat.py` | New provider class with LiteLLM completion + tool loop + graceful fallback | Reduces backend-specific brittleness in tool-calling/runtime behavior |
| `src/rocky/providers/registry.py` | Provider selection now understands LiteLLM | Makes LiteLLM a first-class runtime path |
| `src/rocky/core/runtime_state.py` | Thread state migration to v1.0.0 keys, continuable statuses, stronger continuation scoring, legacy-key compatibility | Fixes the “Rocky stops continuing” failure and preserves older session state |
| `src/rocky/core/router.py` | Continuation resolver now understands `awaiting_user`, `needs_repair`, explicit continuation markers, and “only likely thread” situations | Rocky better distinguishes same-task follow-ups from fresh tasks |
| `src/rocky/util/paths.py` | Added `.rocky/student/knowledge`, `.rocky/student/patterns`, `.rocky/student/examples` | Gives Rocky a dedicated teacher/student state layout |
| `src/rocky/student/store.py` | New persistent notebook/profile/knowledge/pattern/example store | Enables online teachability without hard-coded examples |
| `src/rocky/core/context.py` | Student profile + relevant student notes now enter the context package | Teacher guidance becomes runtime-visible |
| `src/rocky/core/system_prompt.py` | Added “teachable student agent” rules and student-note reuse policy | Aligns runtime behavior with the new notebook layer |
| `src/rocky/app.py` | Student store wiring, `/teach`, student status/inventory/show/add, thread inventory, doctor/status integration | Makes the student layer operational instead of decorative |
| `src/rocky/commands/registry.py` | Added `/teach`, `/student`, `/threads` and help text | Exposes teachability and continuation inspection through the CLI |
| `src/rocky/ui/completion.py` | Dynamic autocomplete for sessions, memory names, student entries, thread ids | Better production TUI ergonomics |
| `src/rocky/ui/repl.py` | Added keyboard shortcuts, richer toolbar, dynamic completer | Faster repeated operator workflows |
| `pyproject.toml` | Bumped to `1.0.0` and added `litellm>=1.78.5` | Ships the intended dependency and version |
| `src/rocky/__init__.py` | Version bump to `1.0.0` | Aligns runtime and packaging |

## Knowledge implemented and what it improves

### 1. “Verified work is often not finished work”

Implemented in:

- `src/rocky/core/runtime_state.py`
- `src/rocky/core/router.py`

Impact:

- A verification pass no longer kills the workflow thread.
- Rocky can continue post-verification steps such as export, cleanup, follow-up analysis, next action, or remediation.
- This directly improves harness continuity.

### 2. “Continuation should be semantic, not just string-match exact”

Implemented in:

- `src/rocky/core/runtime_state.py`
- `src/rocky/core/router.py`

Added signals:

- same workspace
- same execution cwd
- short follow-up prompt
- explicit continuation phrase
- artifact/entity overlap
- task-signature overlap
- unresolved-question overlap
- single likely continuable thread bonus

Impact:

- Better resume behavior for real operator language
- Less reset-to-chat behavior

### 3. “Provider abstractions should not be handwritten per backend forever”

Implemented in:

- `src/rocky/providers/litellm_chat.py`
- `src/rocky/config/*`
- `src/rocky/providers/registry.py`

Impact:

- Rocky now has a more standard adapter layer for local and hosted backends.
- This is especially important when Ollama compatibility differs across chat/tool/reasoning surfaces.

### 4. “Teacher feedback must survive beyond one conversation”

Implemented in:

- `src/rocky/student/store.py`
- `src/rocky/core/context.py`
- `src/rocky/app.py`
- `src/rocky/commands/registry.py`

Impact:

- Rocky can keep durable lessons.
- Rocky can keep domain knowledge, patterns, and examples.
- A human or coding agent can bootstrap Rocky on a few cases, then let Rocky act more independently later.

### 5. “Different reusable knowledge types should not all be forced into one bucket”

Implemented in:

- `profile.md`
- `notebook.jsonl`
- `knowledge/`
- `patterns/`
- `examples/`

Why this matters:

- **Profile** stores stable role/identity/behavior rules.
- **Lessons** store feedback tied to an error or correction.
- **Knowledge** stores durable factual/project notes.
- **Patterns** store reusable extraction/crawling/catalog logic.
- **Examples** store concrete demonstrations.

This separation is important for your catalog/crawling/NER examples because those are not all the same kind of learning.

### 6. “Production terminal UX matters”

Implemented in:

- `src/rocky/ui/completion.py`
- `src/rocky/ui/repl.py`

Impact:

- Faster resume and status inspection
- Better discoverability during long sessions
- Less friction when Rocky is used like a serious terminal tool rather than a demo chatbot

## How this supports your target use cases

### Product catalog agent

Relevant changes:

- student knowledge store
- pattern/example storage
- continuation stability
- LiteLLM provider abstraction

Why it helps:

- Rocky can keep operator corrections about product distinctions
- Rocky can remember disambiguation patterns
- Rocky can carry forward same-task context across multiple cataloging steps

### Crawling agent for roasteries / product sites

Relevant changes:

- pattern store
- example store
- teach command
- continuation routing

Why it helps:

- A teacher can show Rocky what a site pattern looks like
- Rocky can keep extraction/crawler notes as reusable patterns instead of ad hoc chat memory

### NER-like online-trainable agent

Relevant changes:

- student lessons
- examples
- patterns
- learned skills

Why it helps:

- Correction + explanation can now become durable notebook state
- Rocky can accumulate examples and pattern guidance over time

## Known limitations

- No true weight update or fine-tuning is happening; learning is scaffold/state/prompt-level.
- LiteLLM fallback is graceful, but runtime behavior still depends on backend tool-calling quality.
- Student retrieval is intentionally simple and inspectable; it is not a large retrieval stack.
- The harness itself was not expanded into a new phase in this version; instead, Rocky was made more likely to pass the existing continuity and exactness expectations.

## Bottom line

v1.0.0 does not just add features. It changes Rocky’s operating model from:

> tool-using assistant with some memory

into:

> continuable, inspectable, teachable student agent with a more stable provider boundary
