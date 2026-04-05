# Rocky v0.3.0

## Summary

Rocky v0.3.0 is a major runtime architecture upgrade focused on making Rocky a stronger local-model-first general agent.

## Headline changes

- active task threads and continuation-aware routing
- evidence graphs and provenance-bearing claims
- answer contracts for direct, supported answers
- candidate-first project memory and learned behavior promotion
- stronger verification for unsupported claims and answer drift
- thread-aware `/learn` binding
- improved retrieval for memory and learned skills
- hardened generator/oracle harness behavior

## Most important user-visible improvements

- short follow-up prompts are much less likely to fall out of the current task
- unsupported answer narration is less likely to become durable memory
- final answers are more likely to stay focused on the current ask
- learned corrections are more likely to attach to the right workflow family
- traces are much more useful for debugging

## Compatibility notes

- existing CLI workflows remain intact
- existing tool/provider plumbing was preserved where practical
- live agentic tests now skip when the configured local provider is unreachable

## Recommended next step

Read `ROCKY_TUNING_KNOWLEDGE.md` before doing serious local-model tuning or large follow-on refactors.

