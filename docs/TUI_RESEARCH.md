# Rocky TUI research

This file captures the library research that informed Rocky's interaction stack.

## Short conclusion

For a **CLI-first general agent shipping today in Python**, the best stack is:

- **prompt_toolkit** for the input loop and editing model
- **Rich** for rendering markdown, tables, JSON, progress, and syntax-highlighted blocks
- **Textual** as the future full-screen mode when Rocky graduates from a chat-first CLI into a denser operator console
- **OpenTUI** as a future transport/rendering option if Rocky later adopts a TS/Zig frontend or remote client shell

## Comparison

| Library | Strengths | Weaknesses | Rocky decision |
|---|---|---|---|
| prompt_toolkit | best-in-class CLI editing, multiline input, completions, history, vi/emacs modes, mouse support, full-screen capability | lower-level than app frameworks | **ship now** for REPL correctness |
| Rich | excellent markdown, tables, syntax, status, JSON, trace rendering | not a full widget framework | **ship now** for output UX |
| Textual | best Python full-screen TUI framework, async-friendly, testable, terminal + browser target | more opinionated and heavier than a simple REPL | **future full-screen adapter** |
| OpenTUI | terminal-native performance, correctness focus, component model, proven by OpenCode | TS/Zig-oriented, not the fastest path for a Python artifact | **future bridge boundary** |

## Why Rocky ships prompt_toolkit + Rich now

Rocky's product contract puts interactive correctness above flashy layout. The must-have behavior is: line editing, history, slash command completion, interrupts, streamed tokens, readable tables, readable traces, and low startup cost. prompt_toolkit + Rich handles that with a small dependency and runtime footprint.

## Why Textual stays on the roadmap

Textual is the right next step when Rocky needs:

- persistent side panels
- embedded logs and tool traces
- inspector panes
- background task dashboards
- richer keyboard-driven navigation
- snapshot-tested screen layouts

Rocky's internal architecture keeps a transport boundary so the current REPL can later coexist with a Textual UI.

## Why OpenTUI matters

OpenTUI is notable because it treats terminal rendering as a performance and correctness problem rather than as an incidental byproduct of a CLI. That matters if Rocky later gains remote/mobile driving, plugin windows, and a multi-pane control surface.

## Architecture decision

Rocky v0.1.0:

- interaction = `prompt_toolkit`
- rendering = `Rich`
- provider/browser/network/data tools remain plain Python
- UI upgrade path = `Textual`
- alternate native frontend path = `OpenTUI` behind a bridge boundary
