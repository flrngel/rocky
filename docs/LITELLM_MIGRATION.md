# LiteLLM migration notes

## Why switch Rocky toward LiteLLM

Rocky previously relied more heavily on a handwritten OpenAI-compatible chat client.
That works until backend differences in tool-calling, reasoning flags, and response shapes start to matter.

LiteLLM provides a more standard adapter surface across many providers, including Ollama-backed deployments.

## Recommended shape

```text
Rocky -> LiteLLM proxy -> Ollama
```

## New provider style

Rocky now supports:

- `openai_chat`
- `openai_responses`
- `litellm_chat`

Default active provider:

```yaml
active_provider: litellm_local
providers:
  litellm_local:
    style: litellm_chat
    base_url: http://localhost:4000
    model: ollama_chat/qwen3.5:4b
    thinking: true
    reasoning_effort: medium
```

## Notes on Ollama use

- Rocky still supports direct Ollama compatibility mode.
- The preferred v1.0.0 path is to put LiteLLM in front of Ollama.
- The provider code also includes a graceful fallback to the existing chat path if LiteLLM is not installed yet.

## Extra provider fields now supported

- `reasoning_effort`
- `tool_choice`
- `extra_body`

These are useful for local proxy/backends where chat/tool/reasoning surfaces do not line up perfectly.

## Operational takeaway

This migration is not just about convenience.
It reduces the chance that Rocky behaves differently from other frameworks solely because its provider adapter is narrower or more brittle.
