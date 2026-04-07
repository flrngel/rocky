# Student agent design

## Objective

Make Rocky teachable by:

- a human operator
- ChatGPT Codex / Claude Code style coding agents
- project-specific bootstrapping workflows

without hard-coding examples into the source tree.

## Storage model

```text
.rocky/student/
  profile.md
  notebook.jsonl
  knowledge/
  patterns/
  examples/
```

## Knowledge types

| Type | Purpose | Good examples |
|---|---|---|
| `profile` | Stable behavior and role rules | “Rocky is acting as product catalog student for this repo.” |
| `lesson` | Teacher correction on a prior result | “Do not merge these SKUs when the vintage text differs.” |
| `knowledge` | Durable domain/project facts | product naming conventions, site semantics, path rules |
| `pattern` | Reusable procedure or extraction pattern | crawling template, site parser pattern, disambiguation checklist |
| `example` | Concrete worked example | product merge example, labeled NER example |

## Commands

### `/teach <feedback>`

Writes a durable lesson into the student notebook.

Use this when you want Rocky to remember a correction or policy even if you do **not** want a learned `SKILL.md` generated.

### `/learn <feedback>`

Writes student feedback **and** triggers learned skill generation.

Use this when the correction should become both:

- notebook guidance
- reusable workflow skill

### `/student`

Shows notebook status and counts.

### `/student list [kind]`

Lists notebook entries by kind.

### `/student show <entry_id>`

Shows the full stored entry.

### `/student add <kind> <title> <text>`

Manually adds a notebook note.

## Retrieval behavior

Student notes are retrieved by overlap with:

- current prompt keywords
- task signature tokens
- current thread summary
- matching thread id / exact task signature bonuses

The retrieval is intentionally simple and inspectable.

## Why this matters for your examples

### Product catalog disambiguation

Rocky can store:

- exact naming rules
- known ambiguous strings
- examples of “same product” vs “different product”
- operator correction notes

### Crawling/pattern induction

Rocky can store:

- site-specific pattern notes
- selector/field conventions
- worked examples for new sites

### NER-style online teaching

Rocky can store:

- entity boundary corrections
- label definitions
- examples with reasons
- failure classes linked to a task signature

## Philosophy

This is not hidden “memory magic.”
It is **inspectable student state** that a teacher can review, edit, diff, and curate.
