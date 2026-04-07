# Rocky v1.0.1 upgrade report

This report covers the patch from **v1.0.0 → v1.0.1**.

The goal of this patch was narrow and practical: **make Rocky execute tools reliably against Codex-like harness behavior and LiteLLM/Ollama/OpenAI-compatible response shapes**.

## Root causes found

### 1. Tool arguments were treated as if they were always JSON strings
Some backends return function arguments as a dict/object already parsed by the SDK layer. Rocky could mis-handle those payloads.

### 2. Only `tool_calls` was handled robustly
Some stacks still surface deprecated `function_call` fields. Rocky needed to normalize both shapes.

### 3. Assistant tool-call turns can legitimately have `content: null`
Rocky could turn that into the string `"None"`, which pollutes the transcript and can interfere with the next round.

### 4. Tool call IDs were not normalized strongly enough
If the backend omitted an ID or used a non-standard field name, Rocky could lose the linkage between call and tool result.

### 5. Tool schemas were more permissive than they should be
Codex-style tool surfaces tend to be strict and explicit. Rocky benefited from the same direction.

## Files changed

| File | Change | Why it matters |
|---|---|---|
| `src/rocky/providers/openai_chat.py` | Reworked tool-call/message normalization helpers; added fallback support for `function_call`; accepts dict/list/bytes args; preserves null assistant content; synthesizes missing call IDs | Fixes the actual execution path that was stalling or mis-parsing tool calls |
| `src/rocky/providers/litellm_chat.py` | Reused the stronger normalization path for LiteLLM responses and streaming deltas | Makes LiteLLM/Ollama behavior much closer to Rocky’s OpenAI-compatible provider behavior |
| `src/rocky/tools/base.py` | Added `_sanitize_input_schema()` and default `additionalProperties: false` on object schemas | Makes exposed tool contracts stricter and easier for a model/harness to follow |
| `tests/test_openai_chat_provider.py` | Added regressions for dict arguments, null assistant content, and deprecated `function_call` shape | Protects the exact bug class you reported |
| `tests/test_litellm_chat_provider.py` | Added LiteLLM-specific tool-call normalization regression | Ensures the LiteLLM provider stays fixed |
| `tests/test_tool_registry.py` | Added schema-closure regression | Prevents drift back to overly loose tool schemas |
| `pyproject.toml` | Version bump to `1.0.1` | Release identity |
| `src/rocky/__init__.py` | Version bump to `1.0.1` | Runtime/CLI identity |
| `src/rocky/core/system_prompt.py` | Prompt version bump | Keeps runtime prompt/version aligned |
| `README.md` | Added v1.0.1 patch summary | Documents the new tool-execution focus |
| `RELEASE_v1.0.1.md` | New patch release note | Human-readable release summary |

## Codex review influence

I reviewed the local `codex-main` code you provided and pulled two ideas into Rocky’s patch design:

1. **Stricter tool schema surfaces**
   Codex keeps schemas explicit and often closed (`additionalProperties: false`). Rocky now sanitizes tool schemas in the same spirit.

2. **Stable tool-call identity and normalized tool event flow**
   Codex code and tests consistently preserve call IDs through request → tool execution → output. Rocky now normalizes missing/non-standard IDs more defensively.

I did **not** try to clone Codex architecture wholesale. This patch only imported the parts that directly improve Rocky’s current harness/tool loop.

## Validation performed

- targeted provider/tool regression suite passed
- full `pytest` suite passed locally after patching

## Practical effect

This patch does not change Rocky’s top-level student-agent goal. It specifically upgrades the weakest part of v1.0.0:

- Rocky is more likely to actually execute a requested tool call
- Rocky is less likely to break on LiteLLM/Ollama response-shape differences
- Rocky keeps a cleaner assistant/tool transcript during multi-step tool loops
- Rocky exposes stricter tool schemas to reduce model-side ambiguity
