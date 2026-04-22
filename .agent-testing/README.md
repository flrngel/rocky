# Agent-testing assets

This directory keeps the *tracked* pieces of Rocky's live scenario harness:

- `repo-profile.json` — repo-wide harness metadata.
- `specs/*.json` — structured scenario manifests consumed by external `/agent-testing` tooling.

The following paths are intentionally local-only and ignored by git:

- `runs/`
- `evidence/`

Those directories hold per-machine manifests and captured artifacts from live LLM runs.
