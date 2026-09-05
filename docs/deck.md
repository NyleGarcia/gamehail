---
title: Stream Deck / OpenDeck plugin
tags: [deck, opendeck, ipc]
updated: 2026-09-05
---

# Stream Deck / OpenDeck plugin

A deck key is the better trigger for a game assistant: it needs no `input` group, and it
cannot collide with a keybind the game already owns. `dev.gamehail.sdPlugin` follows the
same shape as the OpenWave plugin — python, launched through a `run.sh` that scrubs
OpenDeck's AppImage environment, installed by copy into `~/.config/opendeck/plugins`.

```bash
make deck-install     # validates the manifest, then copies it in
make deck-validate    # manifest paths, icons, python, run.sh syntax
make icons            # regenerate the key art
```

## Actions

| Action | Behaviour |
|---|---|
| **Hold to Ask** | keyDown starts recording, keyUp sends the question. Settings pick the route: answer me, answer me with a screenshot, or answer me and the squad. |
| **Preset Question** | Sends text typed into the property inspector — no microphone. For the question you ask every session. |
| **Mute Speech** | Two-state key, reflects the daemon's actual mute state. |
| **Cancel** | Cuts the answer short and hides the overlay. |
| **Status** | Shows what the daemon is doing, or `offline` when it is not running. |

Hold-to-talk works because a deck sends `keyDown` and `keyUp` as separate events, which
map straight onto the daemon's `press` / `release` — the same two calls the evdev
listener makes, so a deck key and a held hotkey take identical paths.

## The control socket

The daemon listens on a Unix socket (`$XDG_RUNTIME_DIR/gamehail.sock`), one JSON object
per line in each direction:

```json
{"cmd": "press",  "action": "ask_broadcast"}
{"cmd": "ask",    "text": "best price for laranite", "route": "ask_voice"}
{"cmd": "mute",   "on": true}
{"cmd": "status"}
```

`gamehail ctl` is the same protocol from a shell, which makes any macro system a
trigger:

```bash
gamehail ctl press ask_voice
gamehail ctl release ask_voice
gamehail ctl ask --route ask_broadcast "eta to microtech"
gamehail ctl status --json
```

The plugin talks to the socket directly with the standard library (`ghdeck/client.py`)
rather than shelling out to the CLI: OpenDeck runs the plugin under its own interpreter
environment, which cannot see gamehail's venv.

## Failure behaviour

OpenDeck does not restart a plugin that dies — its keys just stop responding, with
nothing to say why — so every handler is wrapped and every failure logged to
`plugin.log`. When the daemon is not running the keys say `offline` instead of failing
silently, and the property inspectors show a warning.
