# Research notes used for Rocky v1.0.0

These notes summarize recent agent papers and practical implications that informed the v1.0.0 redesign.

## Most relevant themes

| Theme | Why it mattered for Rocky |
|---|---|
| harness/scaffolding quality matters a lot | Rocky needed runtime/harness fixes, not only prompt tweaks |
| persistent memory should be structured | teacher feedback needed a notebook, not only chat replay |
| reusable skills are helpful when curated | patterns/examples/skills were separated instead of mixed blindly |
| tool misuse and recovery are real failure modes | continuation + verification + explicit tool rules were strengthened |
| security of skills matters | student state is inspectable files, not opaque auto-executing blobs |

## Papers and implementation links

### Building Effective AI Coding Agents for the Terminal: Scaffolding, Harness, Context Engineering, and Lessons Learned

Implementation influence:

- continuation/runtime scaffolding matters
- terminal-agent success depends on the scaffold, not just the model
- motivated the continuation and TUI/runtime changes

### Memori

Implementation influence:

- persistent memory should be structured and selectively retrieved
- motivated `.rocky/student/` instead of dumping all history back into prompts

### XSkill / reusable knowledge work

Implementation influence:

- different reusable knowledge types matter
- motivated split between knowledge, patterns, examples, and learned skills

### ERL (experiential reflective learning)

Implementation influence:

- reflection + correction loops are useful for adaptation
- motivated `/teach` and feedback-linked notebook entries

### ToolMisuseBench / tool-use benchmarks

Implementation influence:

- tool misuse and recovery failures are common
- motivated stricter shell/system prompt rules and better continuation after verification

### Terminal Agents Suffice for Enterprise Automation

Implementation influence:

- terminal-native agent workflows remain powerful when scaffolded well
- motivated richer terminal ergonomics and stronger continuation behavior

### Towards Secure Agent Skills / Agent Skills in the Wild

Implementation influence:

- skills can become an attack or error surface
- motivated keeping student state as inspectable markdown/jsonl instead of hidden auto-executing memory

## Bottom line

The redesign direction was:

- stronger scaffold
- better provider boundary
- structured memory
- explicit teachability
- inspectable runtime state

That combination is closer to the “student agent” target than just swapping models or adding more prompt text.
