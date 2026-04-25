# Changelog

All notable changes to Rocky are documented here.

## 1.3.0 — 2026-04-21

### Added
- Tracked `.agent-testing/` assets: repo profile, reusable live-learning specs, and companion documentation.
- Machine-readable capability inventory (`docs/capabilities.json`) plus human-readable scenario catalog (`docs/scenarios.md`).
- Release-hygiene assets: `LICENSE`, `CHANGELOG.md`, `CONTRIBUTING.md`, `MANIFEST.in`, `.editorconfig`, and CI workflow.
- Upgrade audit bundle documenting 100 concrete repo problems closed in this pass.
- `scripts/export_capabilities.py` and `scripts/bump_version.py` for repeatable release maintenance.

### Changed
- Version metadata is now centralized in `src/rocky/version.py` and exposed lazily from `rocky.__init__`.
- Package metadata now ships the user-facing `README.md` instead of `AGENTS.md`.
- README status, release references, and operator guidance were refreshed for 1.3.0.
- `.gitignore` now protects local Rocky state and untracked agent-testing run artifacts.

### Fixed
- Deterministic suite failure caused by missing `.agent-testing/specs/*.json` fixtures.
- Release-version drift between package metadata and documentation.
- Root-package eager import of `rocky.core.agent` during `import rocky` for version-only callers.
- Packaging omissions that previously left license, changelog, and tracked harness assets out of source-distribution guarantees.

## 1.2.0 — 2026-04-16

- Previous feature batch: NDJSON streaming, retros CLI, migrate-retros, route/tool docs, trace retention, and research-verifier hardening.
