---
title: Tray and settings UI
tags: [ui, qt]
updated: 2026-09-05
---

# Tray and settings UI

`gamepilot run` puts an icon in the system tray. The icon colour is the state — blue
idle, green listening, amber working — and its tooltip carries the current status, so a
glance says whether it heard you. The menu offers mute, stop speaking, a fresh context
(restarts the warm `claude` session), settings, and quit. `gamepilot settings` opens the
same window without the daemon.

## Settings window

**Audio tab**

- *Microphone* — every capture device PortAudio can see. **Test** records 3 seconds and
  shows the peak level and what Whisper actually heard, which separates "wrong device"
  from "model mis-heard me".
- *Output channels* — one row per channel: on/off, target sink (a combo box populated
  from the live sink list), volume, and the `application.name` the stream carries.
  **Test** speaks through that row's settings before you save them.
- *Which hotkey speaks where* — the route grid. Tick `squad` on `ask_broadcast` and that
  key answers over voice comms; leave it clear on `ask_voice` and that key stays private.

**Assistant tab** — backend mode and model, effort, screenshot and overlay toggles, and
the current hotkey bindings (rebound in the config file; `gamepilot keys` prints names).

## Where settings are written

The window writes to `config.local.toml`, loaded *after* `config.toml` and after its
profile table. A TOML round-trip through a writer drops every comment in the file, so
the hand-written config is never rewritten — the UI's choices simply layer on top of it.
Delete `config.local.toml` to fall back to the file you wrote by hand.

## Threading

Widgets only ever move on the Qt thread. Worker threads put `(kind, payload)` tuples on
a queue; a single `Dispatcher` polls it every 50ms and feeds both the overlay and the
tray. See [[architecture]].
