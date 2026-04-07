# Rocky v1.0.1 release notes

Rocky v1.0.1 is a focused patch release on **tool execution reliability**.

## What was broken

The v1.0.0 student-agent redesign improved continuation, memory, and TUI behavior, but the provider tool loop still had compatibility gaps with LiteLLM/Ollama and some OpenAI-compatible harnesses:

- some backends can return tool arguments as an already-parsed object instead of a JSON string
- some responses still use deprecated `function_call` instead of `tool_calls`
- assistant tool-call turns may have `content: null`
- some tool-call payloads omit a stable `id` unless the client normalizes one

Those gaps could cause Rocky to skip execution, mis-parse arguments, or degrade the follow-up round after a tool call.

## What v1.0.1 fixes

- normalizes both `tool_calls` and fallback `function_call` shapes
- accepts dict, list, bytes, and string tool arguments
- preserves null assistant content for tool-call messages instead of serializing `None`
- synthesizes missing tool call IDs so tool outputs stay linked
- sanitizes tool JSON schema parameters to be more strict and predictable
- adds regression tests for the exact failure shapes above

## Compatibility target

v1.0.1 keeps the v1.0.0 student-agent architecture, but makes the execution path closer to the message/schema discipline used by Codex-style harnesses and modern LiteLLM/OpenAI-compatible tool loops.
