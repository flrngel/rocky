# Rocky — Current State

Last updated: 2026-04-10 (run-20260410-165140)

## Test suite
- 272 deterministic tests, ~10s, zero LLM dependency
- RunFlowManager multi-burst loop covered by 8 dedicated tests in test_run_flow.py (research + non-research paths)
- Integration tests in test_agent_runtime.py use `>=` for call counts to be flow-loop-agnostic

## Agent loop
- Two execution paths in AgentCore.run():
  - Flow-controlled loop (_run_flow_controlled_loop): ALL tasks with tools (except conversation/)
  - Simple provider call: conversation tasks and tasks without tools
- _should_use_flow_loop() gate at agent.py:410 — returns True when route has tool_families AND task_signature is not conversation/
- Non-finalize early return (returning from intermediate tasks) is gated to research/site only — non-research tasks always advance through all tasks before returning

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
