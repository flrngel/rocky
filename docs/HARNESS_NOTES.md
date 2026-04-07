# Harness stability notes for v1.0.0

## Main issue addressed

The biggest harness-facing issue fixed here was premature thread completion.

When Rocky verified a result successfully, it could mark the thread as completed immediately.
That made the next operator turn less likely to continue the same workflow.

## What changed

- thread pass -> `awaiting_user`
- thread fail -> `needs_repair`
- continuation scoring expanded
- legacy session keys are still read
- only-likely-thread situations now get a continuation bonus

## Why this helps the harness

Harnesses often evaluate more than one turn of work:

- do the work
- verify the work
- continue from the result
- make the next exact output
- repair if needed

A runtime that treats “verified once” as “forever finished” is brittle in those situations.

## New operator surfaces useful for harness work

- `/threads`
- `/status`
- `/trace`
- `/why`
- toolbar hints for provider/session/thread
- `Ctrl-R` resume
