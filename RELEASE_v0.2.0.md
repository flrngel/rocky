# Rocky v0.2.0 — Hermes harness melt

## Why this release exists

Rocky’s manifesto says the agent loop is the product: route correctly, use tools, verify, stay legible, and earn trust in a real terminal. v0.2.0 pushes Rocky in that direction by transplanting the most useful *visible* harness ideas from Hermes into Rocky’s simpler file-first core.

This release is intentionally opinionated:

- make evaluation phases explicit
- make current-workdir focus real
- make fresh-session continuation work inside the same project
- keep the whole thing inspectable in files instead of hiding it in opaque state

## Hermes → Rocky transplant map

| Hermes strength | Rocky v0.2.0 implementation |
|---|---|
| Structured handoff / continuity | Session turn summaries + prompt-level project handoffs loaded for fresh sessions |
| Workspace-aware execution | `execution_root` preserved separately from repo root; new commands/files default to the invocation directory |
| Research/eval harness mentality | New `rocky.harness` package with five explicit upgrade phases |
| Scenario catalogs for iterative tuning | Phase 1–3 scenario catalog, phase 4 mini-projects, phase 5 workspace-continuity scenarios |
| Operator-visible inspection | `/harness` command + runtime/meta inventory |

## What changed

### 1) New harness package

Added `src/rocky/harness/` with:

- `HarnessPhase`
- `Scenario`, `MiniProjectScenario`, `WorkspaceContinuityScenario`
- `HarnessResultStore`
- default phase inventory and scenario catalogs

### 2) Five upgrade phases

1. **phase1_route_anchor** — correct route + correct first tool family
2. **phase2_followup_evidence** — continue after the first step and gather sufficient evidence
3. **phase3_end_to_end_contract** — finish the job with answer + trace + verifier outcome
4. **phase4_exact_output_build** — create files, execute them, and compare observed output to requested behavior
5. **phase5_workspace_continuity** — continue project work in a fresh session without losing intent, paths, and recent successful state

### 3) Current-project focus actually works now

Before v0.2.0, Rocky discovered the workspace root but then behaved too much as if the repo root was always the active project.

Now Rocky preserves both:

- `workspace_root` = repository / `.rocky` boundary
- `execution_root` = directory where Rocky was launched

This means:

- shell commands default to the execution directory
- new relative writes prefer the execution directory
- reads can still find existing repo-root files when appropriate
- shell/environment inspection reflects the active execution directory

### 4) Fresh-session handoffs

Added rolling turn summaries to sessions and retrieval of recent workspace handoffs.

New sessions can now receive:

- recent successful task summaries
- important paths
- tool families used
- execution directory context
- short continuation text

The system prompt now includes:

- `## Workspace focus`
- `## Project handoff`

That keeps Rocky grounded in the active project without pretending it remembers unavailable chat turns.

### 5) `/harness` command

New command:

```text
/harness
```

This exposes version, phase inventory, phase descriptions, and scenario counts.

## What was deliberately *not* copied

Some “clever” behaviors seen in recent agent-discussion threads and leak analyses are not good defaults for Rocky.

Not adopted:

- fake tools
- hidden anti-distillation tricks
- covert or deceptive behavior patterns
- opaque always-on inner machinery that the operator cannot inspect

Adopted instead:

- explicit harness structure
- explicit continuity summaries
- operator-visible phase inventory
- file-first inspectability

## Test and scaffold impact

Added tests for:

- subdirectory execution-root behavior
- repo-root read + local write preference
- fresh-session handoff loading
- harness phase inventory
- harness result storage

This release is a scaffold for stronger model/prompt tuning later. The point is to give Rocky better rails, better scenarios, and better continuation structure before deeper provider-specific optimization.

## Version

Rocky is now **v0.2.0**.
