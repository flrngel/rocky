# Rocky Agentic Scenario Repo

This repo is maintained around one bar: prove Rocky through real agentic scenarios, not provider mocks.

## What matters

- Test Rocky through the installed `rocky` CLI, not only through direct Python runtime calls.
- Use the current `ollama` setup unless a task explicitly says otherwise.
- Grade both behavior and result:
  - route selection
  - real tool use from traces
  - final answer quality
  - produced files or observed command output
  - `/learn` persistence and retry behavior in a fresh process
- Prefer generated workspaces and seeded fixtures over fixed product names or hard-coded public cases.
- Treat authored agent skills and `/learn` policies as different systems:
  - real skills are curated workflow files under skill roots
  - learned policies are corrective memory artifacts under `.rocky/policies/learned`
- If a scenario needs teaching, run the baseline turn first, send `/learn`, then retry in a new Rocky process and verify that the learned policy was actually loaded.

## What not to do

- Do not add source-level case logic just to satisfy a scenario.
- Do not treat mock providers as proof of agentic behavior.
- Do not count a scenario as passing if Rocky skipped tools, returned an empty answer, or ignored the learned policy on retry.
- Do not rebuild a docs tree. Keep repo guidance here.

## Scenario workflow

1. Create an isolated temp workspace.
2. Install Rocky from the current checkout.
3. Run the installed CLI against the scenario prompt.
4. Inspect the trace and output.
5. If the scenario is a learning scenario, send `/learn` feedback and retry in a fresh process.
6. Write a phase-by-phase report with what was done, what was expected, and how Rocky actually behaved.
