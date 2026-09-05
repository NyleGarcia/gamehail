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


## Speech recognition

faster-whisper runs on CUDA when it can. Two things make that survivable:

* **CUDA libraries are preloaded by hand.** Arch ships no system cuBLAS, and the pip
  wheels put theirs under `site-packages/nvidia/*/lib`, which the dynamic loader does
  not search. `stt.preload_cuda_libraries()` dlopens each one with `RTLD_GLOBAL` before
  the model is built, which has the same effect as `LD_LIBRARY_PATH` without needing a
  wrapper script.
* **A missing GPU library surfaces late.** ctranslate2 builds the model happily and only
  needs cuBLAS on the first transcription, so a broken setup fails mid-question, not at
  startup. `Transcriber.transcribe` catches that, rebuilds on CPU int8 and answers the
  question rather than losing it.

`[stt] vocabulary` is passed as the decoder's initial prompt. Whisper has never seen
"laranite" and lands on "lara night" without it; the star-citizen profile carries 52
commodity, system, station and ship names.
