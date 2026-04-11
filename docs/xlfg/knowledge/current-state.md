# Rocky — Current State

Last updated: 2026-04-10 (run-20260410-185923)

## Test suite
- 286 deterministic tests, ~9s, zero LLM dependency
- RunFlowManager multi-burst loop covered by 8 dedicated tests in test_run_flow.py (research + non-research paths)
- Integration tests in test_agent_runtime.py use exact `==` call counts
- Web tool tests: 25 tests in test_web_tools.py (search, fetch, bot detection, content extraction, broadening, steps)
- Tool events tests: 6 tests in test_tool_events.py (normalization, browser hints, steps facts)

## Agent loop
- Two execution paths in AgentCore.run():
  - Flow-controlled loop (_run_flow_controlled_loop): ALL tasks with tools (except conversation/)
  - Simple provider call: conversation tasks and tasks without tools
- _should_use_flow_loop() gate at agent.py:410 — returns True when route has tool_families AND task_signature is not conversation/
- Non-finalize early return works for ALL task types with full verification: standard verify → automation judgment → learned constraints → return if pass

## Flow loop task kinds by task type
- research/site → discover/gather/finalize (max_bursts=8)
- repo/shell_execution, automation/general → build/verify/finalize (max_bursts=4)
- extract/data → inspect/produce/finalize (max_bursts=4)
- fallback → inspect/finalize (max_bursts=4)

## advance() heuristics by task kind
- discover: live_pages >= 1
- gather: live_items >= target or live_pages >= 1
- build: any successful tool
- inspect: any successful tool
- produce: any successful tool
- verify: run_shell_command or read_file
- finalize: final_output_ready=True

## Web tool system
- `search_web` in tools/web.py: queries DuckDuckGo (3 endpoints) + Brave, with algorithmic query broadening on zero results (strip site:, drop quotes, drop rightmost token, max 2 rounds). Returns `steps` list in metadata recording each engine attempt and broadening round.
- `fetch_url` in tools/web.py: fetches URL, extracts content using readability-style BS4 parsing (strips nav/header/footer/aside, prefers article/main, falls back to body if < 200 chars). Returns `link_items` with scored links.
- `agent_browser` in tools/browser.py: wraps Vercel `agent-browser` CLI. Separate "browser" tool family with independent permissions.
- Bot detection (`_looks_like_bot_challenge`): hard markers (captcha elements, CF challenge paths) always trigger. Soft markers require ≥2 matches OR 1 match + challenge HTTP status (202/403/429/503). Single soft marker alone does NOT trigger.
- When `fetch_url` hits a bot challenge, result includes `browser_fallback_hint: True` in metadata. `tool_events.py` emits a "Hint: retry with agent_browser" fact for the LLM.
- Tool event summarizers in tool_events.py: separate paths for fetch_url (web_fetch), search_web/extract_links (web_list), agent_browser (shell-like + browser observations). Steps and hint facts are emitted via derive_tool_event_details.
