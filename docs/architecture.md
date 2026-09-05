---
title: Architecture
tags: [architecture]
updated: 2026-09-05
---

# Architecture

One process, three threads plus Qt:

1. **hotkeys** (`hotkeys.py`) — an evdev listener thread reads `/dev/input/event*`
   passively and reports `(action, pressed)`. Passive reads mean the game still
   receives the key. Requires the `input` group.
2. **query** (`pipeline.py`) — spawned per question. Transcribes the recorded audio,
   sends it to the backend, and forwards streamed text to speech and overlay. A
   non-blocking lock drops a second question while one is in flight.
3. **tts** (`tts.py`) — consumes finished sentences from a queue and pipes them
   `piper -> aplay`. Speech therefore starts before Claude has finished writing.
4. **Qt main thread** (`overlay.py`) — owns every widget and polls a `queue.Queue`
   every 50ms. Worker threads only ever put tuples on that queue.

Capture is deliberately split: audio starts on key-down and stops on key-up, while the
screenshot is taken *on key-down* — the frame the player was looking at when they asked,
not the one after they finished the sentence.

See [[backend-modes]] for the Claude CLI wiring.
