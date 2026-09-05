---
title: Architecture
tags: [architecture]
updated: 2026-09-05
---

# Architecture

One process, three threads plus Qt:

1. **triggers** — the control socket (`ipc.py`) is the default one: a deck key,
   `gamepilot ctl`, or any macro system sends `press` / `release`. The evdev listener
   (`hotkeys.py`) is the same thing from a keyboard or HOTAS button and is off unless
   `[hotkeys] enabled` is set, since it needs the `input` group. Both call
   `Pipeline.trigger()`, so neither path is privileged.
2. **query** (`pipeline.py`) — spawned per question. Transcribes the recorded audio,
   sends it to the backend, and forwards streamed text to speech and overlay. A
   non-blocking lock drops a second question while one is in flight.
3. **tts** (`tts.py`) — consumes finished sentences from a queue, synthesises each
   once, and writes the PCM to one `pw-play` per destination channel. Speech starts
   before Claude has finished writing, and the same answer can go to your headset and
   to voice comms at once (see [[audio-channels]]).
4. **Qt main thread** (`ui/app.py`) — owns every widget. A single `Dispatcher` polls
   the event queue every 50ms and feeds both the overlay and the tray; worker threads
   only ever put tuples on that queue. See [[ui]].

Capture is deliberately split: audio starts on key-down and stops on key-up, while the
screenshot is taken *on key-down* — the frame the player was looking at when they asked,
not the one after they finished the sentence.

See [[backend-modes]] for the Claude CLI wiring.
