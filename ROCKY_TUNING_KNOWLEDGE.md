# ROCKY_TUNING_KNOWLEDGE

## Who this is for

This file is for the next engineer or coding agent who needs to tune Rocky on a real local machine.

It assumes you want Rocky to become a practical, local-model-first general agent for real repo, shell, automation, extraction, and data tasks, not a benchmark toy. It is intentionally opinionated and operational.

---

## 1. Rocky v0.3.0 architecture overview

Rocky v0.3.0 is organized around a simple but high-leverage runtime pipeline:

```text
user prompt
  -> session lookup
  -> active-thread / recent-thread lookup
  -> continuation resolution
  -> thread-aware routing
  -> thread + evidence update from prompt
  -> context assembly
  -> model/tool loop
  -> evidence update from tool results
  -> answer contract build
  -> structured verification
  -> memory / learning gating
  -> trace + session persistence
```

The key design change from earlier Rocky generations is that the runtime now has explicit structured state for the current unit of work instead of relying mostly on prompt text, answer text, and generic session summaries.

### Main runtime objects

| Object | Purpose | Persistence level |
|---|---|---|
| `ActiveTaskThread` | Live unit of work across one or more turns | Session metadata |
| `EvidenceGraph` | Structured claims, artifacts, entities, questions, corrections | Session metadata |
| `AnswerContract` | What Rocky is allowed to claim in the final answer | Per-turn trace |
| `CandidateMemory` | Staging area for possible durable project memory | Files in `.rocky/memories/candidates` |
| Learned skill candidate | Workflow correction synthesized from feedback | `SKILL.md` + meta json |

### Architectural center of gravity

Do not think of Rocky as “a prompt + a set of tools.” Think of Rocky as:

- a routing and continuation layer
- a task-thread state layer
- a claim/evidence layer
- a verification and repair layer
- a persistence gating layer
- a retrieval layer for durable memory and learned behaviors

The model is important, but v0.3.0 tries to prevent the model from being the only source of control flow discipline.

---

## 2. Design goals

### Primary goals

- keep short follow-ups attached to the right work
- reduce unsupported claims
- prevent answer prose from poisoning memory
- make `/learn` correct workflows rather than just wording
- keep the system tuneable for local models
- preserve explicit traces and debuggability

### Non-goals

- not a full autonomous planner platform
- not a general world-knowledge graph
- not a distributed multi-agent framework
- not a vector database product
- not a hidden “smartness layer” that only works with very large hosted models

### Design philosophy

When in doubt, prefer:

- explicit runtime structure over implicit prompt conventions
- evidence-backed claims over persuasive narration
- small coherent abstractions over sprawling frameworks
- inspectable file-backed state over hidden services
- candidate-first promotion over eager durability

---

## 3. Component-by-component explanation

## 3.1 `src/rocky/core/runtime_state.py`

This is the most important new file in v0.3.0.

### `ActiveTaskThread`

This represents the live work item. It stores:

- task family and task signature
- workspace root and execution cwd
- route history
- prompt history
- tool history
- artifact refs
- entity refs
- claim ids
- unresolved questions
- answer history
- verification history
- current status

Use this when debugging follow-up failures. If Rocky loses the thread, inspect the latest trace’s `thread.current_thread` payload first.

### `EvidenceGraph`

This is not a full graph database. It is intentionally small and practical. It stores:

- claims
- artifacts
- entities
- questions
- decisions
- corrections

Every claim includes provenance and status. Current provenance types are:

- `tool_observed`
- `user_asserted`
- `agent_inferred`
- `learned_rule`

The system depends on not confusing these.

### `AnswerContractBuilder`

This is the bridge between runtime evidence and final answer behavior. It decides:

- which claims are relevant
- which claims are allowed
- which claims are forbidden
- whether uncertainty is required
- whether delta-answering is required
- whether a structured format is required

This is especially important for local models, because smaller models often replay context or over-answer unless constrained.

### `EvidenceAccumulator`

This normalizes prompt/tool activity into runtime evidence. Right now it is heuristic and lightweight, not a sophisticated parser. That is intentional.

What it does well enough now:

- captures user corrections/preferences/constraints from prompts
- captures paths and some entity hints from prompts
- captures tool-observed paths, commands, stdout snippets, stderr snippets, runtime versions, file writes, file reads, and URLs from tool results

What it does not do yet:

- deep semantic parsing of arbitrary tool payloads
- advanced contradiction reasoning
- structured extraction from every possible tool schema

### `ThreadRegistry`

This persists threads and evidence graphs into session metadata. The data is stored under session meta keys rather than a separate database so that traces and sessions stay easy to inspect and port.

---

## 3.2 `src/rocky/core/router.py`

Routing is now two-part:

1. continuation resolution
2. lexical route selection or inheritance

### Continuation resolver

Continuation is scored using signals like:

- same workspace
- same execution cwd
- short prompt
- reference markers like “continue”, “fix it”, “resume”, “verify it”
- overlap with prior thread artifacts/entities
- overlap with unresolved questions
- active-thread recency

### Why this matters

The main failure mode in smaller local models is not always “bad reasoning.” It is often “the second turn lost the frame.” The continuation layer is there to stabilize the frame before the model is even asked to answer.

### Tuning advice

If Rocky is too sticky and keeps assuming continuation when the user clearly shifted tasks, tune the continuation threshold or new-task markers.

If Rocky is too eager to start new threads, increase scores for artifact/entity overlap and short follow-up prompts.

---

## 3.3 `src/rocky/core/context.py`

Context is now built from:

- instructions
- durable memory
- learned skills
- workspace focus
- handoffs
- thread summary
- evidence summary
- contradiction summary
- answer target

This is a key local-model design choice.

Do **not** dump giant transcripts into the model by default. Instead, compress the active state into:

- what work we are doing
- what evidence we have
- what rules/constraints apply
- what the user is asking now

### Tuning advice

If local models start missing details, do not immediately widen the context with more prose. First inspect whether the missing detail should have been represented as:

- an artifact ref
- a claim
- a memory note
- a learned behavior
- a handoff summary

Only increase raw text context after checking that state encoding is the real issue.

---

## 3.4 `src/rocky/core/system_prompt.py`

The system prompt is still important, but in v0.3.0 it is no longer expected to solve continuation, unsupported claims, or memory poisoning by itself.

### Prompt design principles in v0.3.0

- concise and operational
- explicit about observation > narration
- explicit about workspace path discipline
- explicit about tool-first behavior when relevant tools exist
- explicit about delta-answering on follow-ups
- explicit about uncertainty when support is missing
- structured context blocks instead of one giant monologue

### Local model implication

Smaller local models usually respond better to:

- fewer hidden assumptions
- more explicit constraints
- short structured blocks
- repeated instruction phrasing that stays operational

They usually respond worse to:

- huge policy dumps
- vague “be smart” instructions
- too many nested exceptions

---

## 3.5 `src/rocky/core/verifiers.py`

Verifiers now do more than check that a tool family was used.

### Current verification stages

- route validity / continuation validity
- expected tool use
- tool failure handling
- shell-execution truthfulness
- structured output shape when needed
- automation reporting
- citation checks for certain classes
- claim support
- answer discipline / drift

### Important outputs

Verifier results now include:

- `failure_class`
- `unsupported_claim_ids`
- `missing_evidence_ids`
- `answer_drift_score`
- `memory_promotion_allowed`
- `learning_promotion_allowed`

These outputs matter because v0.3.0 uses verification results to decide:

- whether to retry
- whether to persist memory
- whether to allow learning promotion

### Tuning advice

If Rocky feels too brittle, do **not** remove claim verification first. Instead:

- inspect false positives in `_claim_support`
- improve evidence extraction or token overlap logic
- inspect whether answer contracts are too narrow

If you remove claim checks too early, you will re-open the memory poisoning channel.

---

## 3.6 `src/rocky/memory/store.py`

This file now implements the most important containment behavior in v0.3.0.

### Memory tiers

#### Working memory

- thread-local
- provisional
- can include inference
- lives in thread/evidence state

#### Candidate memory

- extracted after the turn
- not automatically trusted
- carries provenance, stability, contradiction state, supporting claims

#### Durable memory

- project or global
- should be stable, reusable, and evidence-backed
- used for future retrieval and project brief synthesis

### What can be promoted

Rocky v0.3.0 tries to promote:

- stable user constraints/preferences
- stable workspace paths
- workflow rules
- project facts backed by tool evidence
- later, promoted learned rules

It avoids auto-promoting:

- generic answer rhetoric
- speculative interpretations
- one-off environment noise
- unsupported confidence language

### Contradiction handling

Memory notes now have contradiction states such as:

- `active`
- `disputed`
- `superseded`
- `stale`

Current contradiction handling is heuristic, but it already prevents equal-weight retrieval of contradictory notes in many common cases.

### Tuning advice

If memory is too sparse:

- inspect `_classify_text`
- inspect `_candidate_from_supported_claim`
- inspect `_should_promote`

If memory is too noisy:

- tighten `_is_ephemeral`
- tighten supported-claim extraction
- raise stability thresholds in `_should_promote`
- add more contradiction-state demotion

---

## 3.7 `src/rocky/memory/retriever.py`

Retrieval is no longer mostly keyword overlap. It now weights:

- project vs global scope
- kind priority
- task signature match
- provenance strength
- contradiction penalty
- thread overlap
- stability/reusability

### Retrieval tuning order

1. contradiction penalties
2. provenance weights
3. task-signature bonus
4. overlap thresholds
5. limit count

These first four usually matter more than fancy semantic retrieval for the kinds of durable memory Rocky currently stores.

---

## 3.8 `src/rocky/learning/synthesis.py` and `src/rocky/learning/manager.py`

### Learning strategy

Learning in v0.3.0 is explicitly **workflow correction**, not “save the corrected answer.”

Learned skills now record:

- task family / task signature
- failure class
- required behavior
- prohibited behavior
- evidence requirements
- promotion state
- reuse count
- verified success count

### Promotion strategy

Skills start as `candidate`. Verified successful reuse can promote them to `promoted`.

This is important because feedback is another poisoning channel if you treat every learned rule as immediately durable truth.

### Tuning advice

If learned behaviors are not being reused enough:

- inspect `SkillRetriever.retrieve`
- widen task-family/failure-class matching
- inspect generated triggers and keywords
- inspect whether the skill body is too wordy for local models

If learned behaviors are firing too often:

- tighten trigger features
- require stronger task-signature match
- reduce project-scope bonus for generic candidates

---

## 3.9 `src/rocky/session/store.py`

Sessions now store both turn summaries and thread summaries. This matters for fresh-session continuation.

### Why this matters

A future session does not need full prior chat. It usually needs:

- what thread existed
- what artifacts were in scope
- what task signature was active
- what verification outcome occurred
- what paths mattered

That is what thread summaries are for.

### Current limitation

Handoffs are still summaries, not full structured evidence resurrection. Rocky can continue work better than before, but it is still safer to re-ground important machine facts with tools in the new session.

---

## 3.10 `src/rocky/harness/*`

The harness in this repo already had seed-based generation and oracle materialization. v0.3.0 keeps that direction and hardens determinism.

### Current role of the harness

- generate workspaces from seeds
- generate prompts/scenarios from families
- materialize oracles
- support multi-phase evaluation behavior

### What to improve next

The next step is not “more scenarios.” The next step is better grading dimensions for:

- continuation correctness
- claim support coverage
- durable-memory cleanliness
- learning-before/after improvement
- contradiction resolution

---

## 4. End-to-end request lifecycle

This is the real flow you should keep in your head when tuning.

### Step 1: prompt arrives

Rocky loads the current session and session-backed thread state.

### Step 2: continuation resolution

If an active or recent thread seems relevant, Rocky tries to continue/resume it before falling back to generic lexical routing.

### Step 3: route finalization

The route can be:

- inherited from the thread
- adjusted with thread context
- fresh lexical route if no continuation applies

### Step 4: thread/evidence update from the prompt

The prompt may add:

- corrections
- path artifacts
- entity hints
- unresolved questions

### Step 5: context build

The context builder assembles only the most relevant state blocks.

### Step 6: provider/tool loop

The provider either answers directly or uses tools. Tool results are captured as evidence.

### Step 7: answer contract build

Now that Rocky has tool results, it rebuilds the answer contract with the latest evidence.

### Step 8: verification

The final answer is checked for:

- route/tool validity
- unsupported claims
- drift/repetition
- task-specific constraints

### Step 9: repair loop if needed

If verification fails and tools are involved, Rocky can retry with a repair prompt derived from structured verification feedback.

### Step 10: persistence gating

Only after verification does Rocky decide whether to:

- capture candidate memory
- promote durable notes
- record learning-reuse outcomes

### Step 11: trace/session persistence

The trace stores route, continuation, context summary, answer contract, thread snapshot, evidence summary, tool events, and verifier results.

---

## 5. Agent loop behavior and decision points

### Key decisions the loop makes

#### A. Should this continue a thread?

Wrong answer here leads to bad follow-up performance.

#### B. Does the task need tools?

Wrong answer here leads to fabricated answers.

#### C. Has enough evidence been gathered?

Wrong answer here leads to early stopping.

#### D. Can the final answer claim this deterministically?

Wrong answer here leads to hallucinated certainty.

#### E. Should anything become durable memory?

Wrong answer here leads to self-poisoning.

#### F. Should feedback become reusable learning?

Wrong answer here leads to brittle or harmful learned rules.

### What to inspect when Rocky fails

Start with this order:

1. `trace.route`
2. `trace.continuation`
3. `trace.thread`
4. `trace.answer_contract`
5. `trace.supported_claims`
6. `trace.verification`
7. `trace.tool_events`

Do not jump straight to the final answer text. Rocky failures are often upstream state failures.

---

## 6. Prompt/system design principles for local models

### What local models need

- explicit task framing
- short, high-signal context blocks
- explicit tool expectations
- explicit uncertainty behavior
- reduced ambiguity around paths, cwd, and artifacts

### What local models often do poorly

- silently preserving long cross-turn intent
- self-policing unsupported claims without runtime help
- remembering what is provisional vs observed
- avoiding answer repetition when follow-ups are short

### Practical prompt rules in Rocky v0.3.0

- tell the model which tools are exposed
- tell the model the active thread summary
- tell the model the evidence summary
- tell the model not to repeat prior context when not needed
- tell the model when uncertainty is required

### Prompt length and formatting considerations

For smaller local models, prefer:

- clear section headers
- short bullets/lines in context blocks
- fewer than ~10 evidence claims in the prompt block unless absolutely necessary
- direct formatting requirements like “Return valid JSON only”

Avoid:

- giant free-form logs in the system prompt
- including every tool result verbatim when a summary is enough
- stacking too many policy paragraphs before the actual task

---

## 7. Local model assumptions and constraints

Rocky is intentionally local-model-first, but that does not mean every local model will behave equally well.

### What Rocky currently assumes

- the model can follow tool schemas reasonably well
- the model can recover from concise repair prompts
- the model can obey explicit formatting instructions most of the time
- the model benefits from runtime structure more than from huge policy prompts

### Failure classes more common on local models

- continuation lost on short follow-up
- answer drift / recap
- weak tool sequencing
- unsupported certainty after partial evidence
- brittle JSON formatting
- overusing the last visible answer instead of current evidence

Rocky v0.3.0 was designed specifically to reduce these.

---

## 8. Recommended model classes

The exact model names will change quickly. Think in model classes instead.

| Model class | Good at | Weak at | Best Rocky use |
|---|---|---|---|
| Small general instruct (7B-ish) | fast routing, simple repo Q&A, shell inspection, concise summaries | brittle long-horizon continuation, weaker tool sequencing, weaker structured output under pressure | lightweight daily terminal assistant |
| Small/medium coder instruct (7B–14B) | repo reading, code edits, shell tasks, automation scaffolds | sometimes overconfident, may still need strict answer contracts | best default local-only Rocky profile |
| Larger coder/reasoning local model (20B–35B+) | better multi-step tool behavior, better extraction, better repair loops | higher latency, more VRAM, can still drift if prompts are messy | serious local workstation setup |
| Hosted fallback frontier model | strongest recovery, best ambiguous synthesis | cost, privacy, dependency on network | benchmark, difficult tasks, design comparisons |

### Practical guidance

For Rocky, a good coder-oriented local instruct model is usually better than a generic chat model.

If you only can run a smaller model, invest more in:

- tighter answer contracts
- smaller context blocks
- stricter tool expectations
- stronger verifier repair loops

If you can run a larger model, invest more in:

- better evidence extraction
- longer benchmark suites
- more ambitious automation tasks

---

## 9. Tool selection and tool failure handling

### Current important task/tool mappings

| Task signature | Primary tools |
|---|---|
| `repo/shell_execution` | `run_shell_command`, `run_python`, `write_file`, `read_file`, `stat_path` |
| `repo/shell_inspection` | `inspect_shell_environment`, `read_shell_history`, `run_shell_command` |
| `local/runtime_inspection` | `inspect_runtime_versions`, `run_shell_command` |
| `repo/general` | `grep_files`, `read_file`, `list_files`, `git_*`, `run_python` |
| `data/spreadsheet/analysis` | `inspect_spreadsheet`, `read_sheet_range`, `run_python` |
| `extract/general` | `glob_paths`, `stat_path`, `run_python`, `read_file` |
| `automation/general` | `write_file`, `read_file`, `run_shell_command` |

### Tool-failure handling philosophy

A tool failure should become observed runtime evidence, not just disappear.

That is why failed tool results can still create provisional claims in the evidence graph.

### When to retry

Retry when:

- provider transient failure
- tool sequencing failure that verification can explain
- recoverable shell failure (e.g. execute script via interpreter)
- structured output repair opportunity

Do not retry endlessly when:

- the tool output shows a real external auth/permission/network failure
- the user’s requested evidence genuinely cannot be obtained from this environment
- the answer contract still has missing evidence after reasonable attempts

### Common tuning opportunities

- better parsing of `run_shell_command` payloads
- better extraction of meaningful facts from `run_python` output
- more task-specific allowed tool sets
- better repair prompts when verification fails

---

## 10. Memory strategy and tuning knobs

### Current memory knobs that matter most

1. what kinds of supported claims become candidates
2. promotion thresholds
3. contradiction handling
4. retrieval weighting
5. project brief synthesis policy

### First things to tune

#### First

- `_candidate_from_supported_claim`
- `_should_promote`
- `PROVENANCE_PROMOTION_SCORE`
- `CONTRADICTION_PENALTY`

#### Second

- `_classify_text`
- `_is_ephemeral`
- retrieval overlap thresholds
- project brief grouping and ordering

#### Third

- contradiction resolution rules
- cross-session memory decay / stale handling
- explicit demotion policies for notes repeatedly contradicted

### Memory tuning tradeoff

If you promote too little:

- Rocky forgets useful paths, rules, and constraints
- fresh-session continuation suffers

If you promote too much:

- Rocky self-poisons
- stale facts dominate retrieval
- learned errors become “project truth”

When unsure, bias slightly toward under-promotion.

---

## 11. Planning depth and decomposition tradeoffs

Rocky v0.3.0 is deliberately not built around a heavyweight explicit planner module. Instead, the decomposition is embedded in:

- route-specific tool expectations
- verification failures
- repair prompts
- answer-contract constraints

### Why this is intentional

For local models, a brittle explicit planner often adds latency and error surfaces without improving task completion enough.

### When deeper planning is worth adding later

- long-horizon automation projects
- tasks with explicit dependency graphs
- multi-artifact builds requiring staging
- tasks where verification can decompose the remaining work clearly

### What not to build too early

- recursive subagent trees
- planner/executor/reviewer stacks for every task
- hidden “reflection agents” whose outputs are not inspected or tested

---

## 12. Retry behavior and guardrails

### Current retries

- provider transient retries
- structured output repair for extraction
- verification-driven tool retries for tool-backed routes

### Guardrails already present

- automation shell-write guard blocks shell-based file creation before `write_file`
- unsupported claim checks can fail answers
- memory promotion can be disabled by verification
- learned behavior promotion is candidate-first

### Tuning advice

If retries feel too weak:

- improve repair prompts with more specific missing evidence lists
- pass verifier failure class into more route-specific repair guidance
- add route-specific evidence sufficiency counters

If retries feel too slow:

- reduce max rounds for small models
- fail faster on external/live-source hard failures
- reduce repeated shell-only loops

---

## 13. Latency vs quality tradeoffs

### Biggest latency drivers

- provider model size
- number of tool rounds
- amount of tool output text surfaced to the model
- repeated verification repair loops
- browser/web tools

### High-quality but expensive behaviors

- larger local model
- extra confirmation shell step
- reread-after-write verification
- more evidence claims in context
- multiple retries

### Fast but risky behaviors

- direct answer without tools
- shallow tool use
- no repair loop
- permissive memory promotion
- large handoff summaries without re-grounding

### Recommendation

For local use, the highest ROI is usually:

- one good coder model
- strict task/tool routing
- moderate tool rounds
- compact evidence summaries
- strong verifier gating

not giant multi-pass reasoning loops.

---

## 14. Determinism vs creativity tradeoffs

### Areas that should be deterministic-ish

- routing
- continuation resolution
- tool exposure
- memory promotion
- learning promotion
- structured extraction output shape
- verification outcomes

### Areas that can tolerate more creativity

- narrative summaries after evidence is established
- README/docs drafting
- alternative automation structure proposals
- research synthesis after evidence gathering

### Practical tuning

If Rocky becomes flaky, lower creativity in these ways first:

- reduce provider temperature
- tighten answer contracts
- tighten prompt wording around exact formats
- reduce optional context blocks
- strengthen claim support verification

---

## 15. How to diagnose common failures

### Failure: short follow-up became generic chat

Inspect:

- continuation score reasons
- active thread artifact/entity refs
- unresolved questions
- whether the last useful thread was marked active/completed/failed incorrectly

Likely fixes:

- continuation threshold
- richer thread summary tokens
- stronger artifact/entity extraction from tool results

### Failure: answer sounds confident but unsupported

Inspect:

- `trace.answer_contract`
- `trace.supported_claims`
- verifier `unsupported_claim_ids`
- evidence accumulator coverage

Likely fixes:

- improve tool-event fact extraction
- tighten answer contract forbidden-claim set
- strengthen `_claim_support`

### Failure: memory polluted with wrong rule/fact

Inspect:

- `trace.verification.memory_promotion_allowed`
- candidate note payloads in `.rocky/memories/candidates`
- auto notes in `.rocky/memories/auto`
- project brief contents

Likely fixes:

- tighten `_should_promote`
- expand `_is_ephemeral`
- improve contradiction handling

### Failure: `/learn` helped the wrong workflow

Inspect:

- `last_trace.thread.current_thread`
- learned skill metadata in `SKILL.meta.json`
- retrieval triggers in skill metadata

Likely fixes:

- better task family / thread lineage binding
- tighter trigger generation
- stronger failure-class matching

### Failure: local model keeps recapping prior context

Inspect:

- `answer_contract.do_not_repeat_context`
- prior answer length
- `answer_drift_score`

Likely fixes:

- make follow-up prompts trigger delta-answering more often
- reduce handoff/instruction verbosity
- tighten rewrite-on-drift policy

---

## 16. How to evaluate real-world performance

Use three layers of evaluation.

### Layer 1: fast regression suite

Run:

```bash
pytest -q
```

This should stay green before and after tuning changes.

### Layer 2: generated harness runs

Use the seed-based harness families to inspect:

- routing
- tool use
- generated workspace truth
- automation correctness
- continuation behavior

### Layer 3: operator task battery on your machine

You need a real local task suite. Suggested categories:

- repo understanding
- shell/runtime inspection
- spreadsheet inspection
- extraction to JSON
- small automation builds
- multi-turn follow-up tasks
- correction + `/learn` + rerun

Track:

- task completion rate
- unsupported claim rate
- unnecessary repetition rate
- bad-memory incidents
- learned-skill helpfulness rate

---

## 17. Suggested benchmark/task suite for Rocky

Build a real local benchmark folder with 10–20 tasks per family.

### Repo understanding

- find implementation of a function
- explain a module from direct file reads
- locate config precedence rules
- identify changed files + likely risk areas

### Shell/runtime inspection

- detect installed runtime versions
- inspect shell history
- verify executable paths
- run a workspace script and inspect output

### Structured extraction

- extract JSON from logs
- parse JSONL
- summarize CSV shape then answer one numeric question
- extract entities from text files without creating output files unless asked

### Automation build/use

- create script, reread script, verify exact output
- create two-file mini-project and verify command output
- correct broken script based on observed output

### Continuation / learning

- task with one short follow-up
- task with correction after a bad first answer
- `/learn` followed by fresh-session rerun
- handoff-based continuation next session

### Poisoning resistance

- prior wrong answer should not become project memory
- explicit user correction should supersede weaker inference
- stale path should not dominate when contradicted

---

## 18. Suggested manual test scenarios

Run these manually on your actual machine.

1. Ask Rocky to run a local workspace script that emits JSON, then ask a short follow-up: “which ids should merge?”
2. Ask Rocky to create a small script, verify it, then ask: “change it to handle empty input too.”
3. Ask Rocky about installed runtimes and then follow up: “where is the python binary?”
4. Force a bad answer, correct Rocky with `/learn`, then rerun in a fresh session.
5. Create conflicting path facts and make sure only the correct one survives retrieval.
6. Ask Rocky to summarize a repo, then ask a short follow-up referencing “that module” or “that file.”

These tests expose the real continuation and evidence behavior better than one-shot prompts do.

---

## 19. Suggested automated evals to add next

1. **Continuation correctness grader**
   - generate short follow-up prompts against active threads
   - score whether the inherited task family was correct

2. **Claim support coverage metric**
   - compare final answer sentences to allowed supported claims

3. **Memory cleanliness metric**
   - inspect whether any promoted notes map only to answer prose or inferred claims

4. **Learning-before/after eval**
   - run baseline
   - inject correction and `/learn`
   - rerun fresh session on a related task
   - compare verifier outcomes

5. **Contradiction stress eval**
   - generate conflicting facts
   - check retrieval and brief synthesis behavior

---

## 20. Suggested ablations to run

When tuning, run one ablation at a time.

### High-value ablations

- continuation enabled vs disabled
- answer contract enabled vs disabled
- claim support verification enabled vs disabled
- supported-claim memory extraction vs prompt-only fallback
- learned-skill retrieval enabled vs disabled
- project brief injected vs omitted

### What each ablation tells you

- continuation ablation -> whether multi-turn gains come from thread state or luck
- answer contract ablation -> how much answer discipline is helping
- verifier ablation -> how much hallucination containment you are buying
- memory extraction ablation -> whether safe memory gating is too sparse or appropriately strict
- learned retrieval ablation -> whether learned skills are actually helping or just adding noise

---

## 21. Prioritized tuning roadmap

### Phase A: make local defaults strong

- pick a better default local coder model for your hardware
- run manual continuation tests
- tighten continuation thresholds if needed
- tune answer-contract and claim-support thresholds

### Phase B: improve evidence quality

- improve tool-output normalization
- make more tool results emit higher-quality claims
- add route-specific evidence sufficiency signals

### Phase C: improve learning quality

- make failure-class inference better
- improve learned-skill retrieval precision
- add promotion demotion rules beyond simple verified success count

### Phase D: improve long-horizon behavior

- add thread summarization / compaction
- add stale-thread lazy loading
- add more deliberate follow-up state transitions

---

## 22. Highest leverage next 10 improvements

1. Improve evidence extraction from `run_shell_command` and `run_python` payloads.
2. Add a small route-specific evidence sufficiency scorer before final answer.
3. Add a lightweight thread-compaction mechanism for long sessions.
4. Add more precise contradiction detection for paths and workflow rules.
5. Improve learned-skill trigger synthesis from thread structure, not just tokens.
6. Add harness graders for memory cleanliness and learning improvement.
7. Make answer-drift rewriting more aggressive for short follow-ups.
8. Add a proper “resume recent thread” bias when a fresh session stays in the same workspace subtree.
9. Add optional model-specific system-prompt profiles for smaller vs larger local models.
10. Add telemetry summaries over traces so tuning does not require reading raw JSON every time.

---

## 23. Things that look tempting but are probably a waste

1. Giant all-purpose system prompts trying to solve everything.
2. Immediate vector-database integration for every state surface.
3. Recursive self-reflection loops for routine tasks.
4. Dozens of domain-specific routes before the core continuation/evidence path is solid.
5. Multi-agent decomposition for simple repo/shell/automation work.
6. Fancy memory embeddings before contradiction and promotion policies are stable.
7. Large static scenario catalogs that the model can overfit.
8. Overfitting tool order requirements too tightly unless correctness requires them.
9. Adding new tools before making current tool evidence extraction better.
10. Treating benchmark pass rate as success while manual continuation tasks still fail.

---

## 24. Logs and telemetry to inspect during tuning

Start with the trace JSON in `.rocky/traces/`.

### Most valuable fields

- `route`
- `continuation`
- `context`
- `thread`
- `answer_contract`
- `supported_claims`
- `verification`
- `tool_events`

### Questions to ask while reading traces

- Did Rocky continue the right thread?
- Did the tool loop gather enough evidence?
- Were the supported claims actually sufficient for the answer?
- Did the answer contract forbid the right temptations?
- Did verification block memory promotion when it should?
- Did the final answer still drift or over-recap?

### Telemetry that should probably be aggregated later

- continuation hit rate
- unsupported claim rate
- answer drift average
- candidate-to-promoted memory ratio
- learned-skill retrieval hit rate
- learned-skill verified-success promotion rate

---

## 25. Config parameters to tune first, second, and third

### First

| Knob | Where | Why |
|---|---|---|
| provider model | `config.yaml` | biggest quality lever |
| provider temperature | `config.yaml` | biggest determinism lever |
| provider thinking | `config.yaml` | can help or hurt depending on model |
| tool output size | `tools.max_tool_output_chars` | affects local-model overload |

### Second

| Knob | Where | Why |
|---|---|---|
| shell/python timeout | `tools.shell_timeout_s`, `tools.python_timeout_s` | affects automation robustness |
| continuation thresholds/signals | `router.py` | affects multi-turn reliability |
| verifier thresholds | `verifiers.py` | affects hallucination vs strictness balance |
| memory promotion thresholds | `memory/store.py` | affects memory cleanliness |

### Third

| Knob | Where | Why |
|---|---|---|
| learned-skill retrieval weights | `skills/retriever.py` | affects learned behavior usefulness |
| handoff retrieval heuristics | `session/store.py` | affects fresh-session continuation |
| project brief synthesis policy | `memory/store.py` | affects long-term workspace guidance |

---

## 26. How Codex should safely modify the codebase next

When making changes, follow this order.

1. Write or update a failing test first if possible.
2. Inspect traces for the failing behavior.
3. Decide whether the issue is in routing, evidence, verification, retrieval, or provider behavior.
4. Change the smallest layer that can fix the root cause.
5. Re-run targeted tests, then full `pytest -q`.
6. Keep new runtime state explicit and serializable.

### Safe modification rules

- avoid burying important state in provider-only prompt text
- avoid making memory promotion more permissive without adding tests
- avoid changing route/task-signature names casually because retrieval and harness assets rely on them
- avoid adding provider-specific hacks in core runtime unless isolated behind config or provider adapters
- avoid making learned-skill retrieval too broad without regression tests

### Preferred extension pattern

- add structured state
- surface it in context
- add verification for it
- add tests for it

That pattern is much safer than “just add one more prompt instruction.”

---

## 27. Where the architecture is intentionally incomplete

These are known provisional areas, not accidents.

### Evidence parsing is still heuristic

The evidence graph is useful, but it is not a deep semantic parser. That is okay for v0.3.0.

### Contradiction handling is still shallow

It prevents some poisoning, but it is not a formal truth-maintenance system.

### Learned-skill promotion is simple

Promotion currently keys mostly off verified successful reuse count. Later versions should incorporate more negative evidence and regression checks.

### No explicit long-horizon planner

This is deliberate for now. Add it only if repeated real tasks show that route/tool/verification decomposition is not enough.

### No special local-model profiles yet

The runtime is local-friendly, but it does not yet ship separate prompt/verifier profiles for small vs medium vs large local models.

---

## 28. Known risks, weak points, and failure modes

1. Evidence extraction may miss the most important fact in a messy tool payload.
2. Continuation may still fail when overlap is semantic rather than lexical.
3. Claim support heuristics may create false positives or false negatives on nuanced answers.
4. Candidate memory may still under-capture some genuinely useful project facts.
5. Learned-skill triggers may be too keyword-biased in some cases.
6. Long sessions can still become context-heavy before compaction exists.
7. Local models may still underperform on exact JSON formatting without repair loops.
8. Handoffs are summaries, so stale state can still leak if re-grounding is skipped.

Treat these as active tuning targets.

---

## 29. Practical guidance for running Rocky on an actual local machine

### Baseline setup

- start with one stable local provider endpoint
- choose one primary coder-oriented model
- keep permission mode supervised until you trust the setup
- keep traces enabled
- run in a real repo or realistic workspace, not only synthetic tasks

### First week plan

Day 1–2:

- confirm repo/shell/runtime routes
- run manual continuation tests
- inspect traces after every failure

Day 3–4:

- run automation and extraction tasks
- test memory cleanliness
- test `/learn` + rerun

Day 5–7:

- compare 2–3 local models on the same task battery
- tune prompt size, tool output size, and verifier strictness

### Operating principle

Always keep one known-good baseline config so you can tell whether a tuning change helped or just moved the error around.

---

## 30. Practical guidance for model swapping

When swapping models, do not only compare final answer quality. Compare:

- route correctness
- continuation stability
- tool-call quality
- structured output compliance
- unsupported claim rate
- retry frequency
- answer drift rate

### Small model profile

Use:

- lower temperature
- tighter tool scopes
- smaller context blocks
- stronger structured-output repair
- aggressive delta-answering

### Larger local model profile

Use:

- slightly richer evidence summaries
- slightly deeper tool loops
- more ambitious automation tasks
- broader learned-skill retrieval

### Hosted comparison profile

Use it mainly to separate architecture issues from local-model limitations.

If hosted and local both fail the same way, the problem is probably Rocky’s runtime design, not the local model.

---

## 31. Practical guidance for adding new tools

When adding a tool, do all of the following.

1. Add the tool implementation and schema.
2. Add it to the tool registry.
3. Decide which task signatures can use it.
4. Decide whether it is read-only or state-changing.
5. Add evidence extraction support for its outputs.
6. Add verifier expectations if the tool changes task semantics.
7. Add tests.

### Most common mistake

Adding a tool without teaching `EvidenceAccumulator` how to convert its output into artifacts/entities/claims. If you skip that, the tool may work, but the rest of Rocky will still behave as if nothing solid was observed.

### Good tool additions for Rocky

- tools that produce deterministic, structured, machine-observable results
- tools that replace brittle shell parsing with clean schemas
- tools that help local models avoid long text parsing

### Lower-priority tool additions

- tools whose output is mostly giant prose blobs
- tools that duplicate existing shell/python capability without adding structure

---

## 32. Practical guidance for improving memory and long-horizon behavior

### Short term

- improve supported-claim extraction
- improve contradiction handling
- add better thread summaries
- add stale-thread loading policies

### Medium term

- add thread compaction / summarization after N turns
- separate recent hot thread state from cold archived thread state
- add explicit user-confirmed durable rules distinct from heuristic promotion

### Long term

- add better cross-session thread lineage
- add more structured project-brief synthesis
- add memory-demotion on repeated contradiction

### Important warning

Do not try to solve long-horizon behavior by dumping more chat history into the model. That usually hurts smaller local models more than it helps.

---

## 33. Practical guidance for improving task success rate

The biggest levers are usually not exotic.

### In order of leverage

1. better local model choice
2. tighter continuation behavior
3. better tool evidence extraction
4. better answer contracts
5. stronger repair prompts
6. better learned-skill retrieval precision
7. cleaner durable memory

### What to measure

- first-pass success rate
- success after one repair loop
- tasks needing tools but answered without them
- unsupported deterministic claims
- short follow-up continuity success

If you improve success rate but unsupported claims spike, you did not really improve Rocky.

---

## 34. Practical guidance for regression prevention

### Minimum release checklist

Before shipping a behavior change:

- run `pytest -q`
- run a continuation manual test
- run one automation build/verify manual test
- run one extraction-to-JSON manual test
- inspect memory candidates and project brief after those tasks
- test `/learn` on one correction and rerun

### Add tests when changing

- routing / continuation logic
- verifier thresholds
- memory promotion
- learning binding / promotion
- harness materialization/oracles

### Golden rule

Every time you make Rocky “more capable,” ask whether you also made it easier for unsupported narration to become durable truth. If the answer might be yes, stop and add a containment test.

---

## 35. Final operator guidance

Rocky v0.3.0 is deliberately not trying to be magical. Its strength comes from explicit state, explicit evidence, explicit verification, and explicit persistence gates.

When tuning it, keep pushing in that direction.

If a proposed change makes Rocky feel smarter but less inspectable, more implicit, more prompt-dependent, or more eager to save speculative state, it is probably the wrong direction.

If a proposed change makes Rocky easier to inspect, easier to debug, more grounded in observed evidence, and easier to adapt to different local models, it is probably the right direction.

