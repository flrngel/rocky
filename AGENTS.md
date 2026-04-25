# Rocky Agentic Scenario Repo

Prove Rocky through real agentic scenarios, not provider mocks.

## Tests

**Deterministic** — every commit must stay green:

    ./.venv/bin/pytest -q

**Live-LLM** — uses real Ollama per `~/.config/rocky/config.yaml`:

    ROCKY_LLM_SMOKE=1 ROCKY_BIN=./.venv/bin/rocky ./.venv/bin/pytest tests/agent/test_self_learn_live.py -v

Or via `/agent-testing` with a structured run manifest:

    python3 .agents/skills/agent-testing/scripts/run_eval.py \
      --repo . --spec .agent-testing/specs/sl-all.json \
      --out .agent-testing/runs/$(date -u +%Y%m%dT%H%M%SZ)-<label>.json

Layout: `tests/agent/` (live tests + `_helpers.py` with explicit `__all__`), `.agent-testing/{repo-profile.json, specs/, runs/, evidence/}` (tracked specs/profile; `runs/` and `evidence/` ignored local outputs).

## L20 — when live-LLM A/B is required

Any change to these paths needs a **triple-live A/B** (3× with change, 3× without) before shipping. Deterministic mechanism proof is not sufficient:

- `src/rocky/learning/policies.py` (`LearnedPolicyRetriever`, `WEAK_MATCH_TOKENS`)
- `src/rocky/learning/ledger_retriever.py` (scoring)
- `src/rocky/core/context.py` (`ContextBuilder._build_policies`)
- `src/rocky/core/agent.py` (`_maybe_upgrade_route_from_project_context`, `_route_upgrade_driving_policy`, `_promote_policy_meta` wiring)

F1 taught us this (see `docs/xlfg/runs/20260416-190523-next-steps/`): clean deterministic tests, but live A/B revealed it amplified a Phase-2 derived-autonomous leak. The fix was reverted.

Historical variance band: 34–36/36 on the 12-scenario live suite. Below 34 is a regression; 34–36 is within noise.

## Decision rule

| Change | Deterministic | Live-LLM A/B |
|---|---|---|
| Retrieval / scoring / context / learning paths (above) | required | **triple-live, both sides** |
| New live scenario | required | triple-live with new scenario |
| Refactor, CLI, tool, config, UI, docs, tests | required | — |

## Rules

- **Sensitivity witness** for every code change: revert → test fails → restore → passes. A test that doesn't bite is not proof.
- **Learning scenarios**: baseline → `/learn` → retry in **fresh process** → verify policy loaded. Never reuse the subprocess.
- Test through the installed `rocky` CLI, not only direct Python calls.
- Don't mock providers for agentic tests. Don't add case-specific source logic to satisfy a scenario. Don't weaken tests to get green.
- Prefer generated workspaces over hard-coded fixtures.
- Skills (curated `SKILL.md` files) and learned policies (`.rocky/policies/learned/`) are different systems.
