# Rocky — Current State

Last updated: 2026-04-10 (run-20260410-162652)

## Test suite
- 268 deterministic tests, ~7s, zero LLM dependency
- test_live_agentic_provider.py deleted (was the only real-LLM test)
- RunFlowManager multi-burst loop covered by 4 dedicated tests in test_run_flow.py

## Agent loop
- Two execution paths in AgentCore.run():
  - Flow-controlled loop (_run_flow_controlled_loop): research/site tasks only, RunFlowManager with FlowTask tree (T1→T2→T3), advance(), context carry-forward
  - Simple provider call: everything else, no step tracking
- _should_use_flow_loop() gate at agent.py:410 — returns True only for research/site task signatures
- advance() heuristics are research-domain-specific (live_pages, link_items, minimum_list_items)

## Testing RunFlowManager
- Can be tested in isolation: tmp_path + synthetic events + EvidenceGraph — no provider, no LLM
- Event shape for advance(): fetch_url with artifacts[{kind:"url", ref:url}] + facts[{kind:"link_item", text:...}]
- T1 (discover): advances on live_pages >= 1
- T2 (gather): advances on live_pages >= 1 or live_items >= 4
- T3 (finalize): advances only on final_output_ready=True
