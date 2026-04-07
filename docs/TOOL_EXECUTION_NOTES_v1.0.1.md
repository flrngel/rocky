# Tool execution notes for Rocky v1.0.1

## Why the patch was necessary

The student-agent redesign in v1.0.0 improved continuity and learnability, but tool execution still depended on a narrower set of backend response shapes than real LiteLLM/Ollama/OpenAI-compatible stacks often return.

## Concrete cases now handled

- `tool_calls` with `function.arguments` as a JSON string
- `tool_calls` with `function.arguments` already parsed as a dict
- deprecated single `function_call` responses
- assistant tool-call messages where `content` is `null`
- missing tool-call IDs that need local synthesis
- non-string tool outputs that must be serialized before reinjection into conversation history

## What changed in code

### `OpenAIChatProvider`

Added a normalization layer around:

- response choice extraction
- assistant message content extraction
- tool call collection
- argument parsing
- tool output serialization

### `LiteLLMChatProvider`

Changed LiteLLM handling to reuse the same normalization rules rather than assuming one canonical SDK shape.

### Tool schemas

Sanitized tool input JSON schemas so object schemas default to `additionalProperties: false` and inferred object/array types are made explicit when missing.

## Test coverage added

- OpenAI-compatible dict-argument regression
- deprecated `function_call` regression
- LiteLLM normalization regression
- strict tool-schema regression
