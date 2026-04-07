# Rocky v1.0.0 release notes

## Summary

Rocky v1.0.0 is the first version aimed at the **student-agent** goal:

- it is easier to teach
- it is better at resuming work
- it is more stable with harness-style tool loops
- it treats LiteLLM as the main provider abstraction
- it has a more production-ready terminal UX

## Headline changes

### 1. LiteLLM-first runtime

Rocky now supports `litellm_chat` as a first-class provider style and ships with `litellm_local` as the default active provider.

This is the recommended path when Ollama is still the real backend but you want Rocky to speak through a more standard provider layer.

### 2. Continuation no longer dies after a pass

In earlier versions, a successful verification could mark a thread as `completed` immediately.
That made the next prompt less likely to continue the same workflow.

In v1.0.0, successful work moves a thread to `awaiting_user` instead. Follow-up prompts such as:

- `continue`
- `keep going`
- `what next`
- `finish it`

can resume the same task more reliably.

### 3. Student notebook

Rocky now has a durable student layer under `.rocky/student/`:

- `profile.md`
- `notebook.jsonl`
- `knowledge/`
- `patterns/`
- `examples/`

This supports a teacher → student operating model without hard-coding your examples.

### 4. New commands

- `/teach <feedback>`
- `/student`
- `/student list [kind]`
- `/student show <entry_id>`
- `/student add <kind> <title> <text>`
- `/threads`

### 5. Better TUI UX

- dynamic autocomplete for sessions, student entries, memory names, and thread ids
- keyboard shortcuts for resume/new/status/freeze/student
- toolbar now shows runtime state, not just a static hint string

## Compatibility notes

- Old thread metadata is still read through legacy keys and rewritten into the new v1.0.0 keys.
- OpenAI-compatible chat and responses providers still work.
- If LiteLLM is not installed, the LiteLLM provider falls back to the existing chat provider behavior.

## Recommended deployment shape

Use Rocky → LiteLLM → Ollama.

That keeps your local backend unchanged while making Rocky less dependent on Ollama-specific compatibility quirks.
