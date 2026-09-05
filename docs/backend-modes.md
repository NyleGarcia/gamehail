---
title: Backend modes
tags: [backend, claude]
updated: 2026-09-05
---

# Backend modes

Both modes run the `claude` CLI headless, so cost comes out of the Claude Code
subscription rather than API credits. They share `build_argv()` in
`backends/claude_cli.py`.

Common flags and why:

| flag | reason |
|---|---|
| `-p --output-format stream-json --include-partial-messages --verbose` | token-level deltas, so TTS can start mid-answer |
| `--system-prompt` + `--setting-sources ""` + `--disable-slash-commands` | replaces the CLAUDE.md / skills preamble (~19k input tokens measured) with a short in-game prompt |
| `--restricted` | removes Bash and the other code-running tools; the assistant only needs MCP + `Read` |
| `--permission-mode dontAsk --permission-prompts none` | nothing can block on a prompt nobody will see mid-game |
| `--allowed-tools` | explicit allow list, e.g. `Read mcp__scmcp` |
| `--no-session-persistence` | game chatter does not belong in the resume picker |

## oneshot

`claude -p … --session-id <uuid> "<question>"` per question, stdout parsed until the
`result` event. Stateless; each question re-pays the prompt-cache write.

## persistent

One process started with `--input-format stream-json`; each question is written to
stdin as a `{"type":"user", …}` line and stdout is drained until that turn's `result`.

Measured here: 2.9s for the first turn, 1.07s for the second, with 13.4k tokens served
from cache. The process is recycled after `backend.max_turns` questions, and restarted
automatically if the pipe breaks.
