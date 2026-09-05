# gamepilot

Hotkey gaming assistant for Linux. Hold a key, ask a question out loud, get a spoken
answer plus an on-screen overlay — answered by Claude Code running headless against a
game's MCP server (e.g. [SCMCP](https://github.com/) for Star Citizen).

No API key, no per-token bill: it drives the `claude` CLI, so it runs on your existing
Claude Code subscription.

```
  hold a deck key ─┐
  gamepilot ctl ───┼─► mic ──► whisper ──► claude -p (+ game MCP) ─┬─► piper ──► "me"    (your headset)
  (keyboard: opt-in)│      + screenshot ──┘                        ├─► piper ──► "squad" (OpenWave Chat Mix ──► Discord)
                    │                                              └─► Qt overlay + tray
```

## Two backend modes

| mode | what it does | first answer | follow-ups | context |
|---|---|---|---|---|
| `persistent` (default) | one long-lived `claude` process fed over stdin | ~3s | **~1s** (warm prompt cache) | carries over between questions |
| `oneshot` | fresh `claude -p` per question | ~3s | ~3s | none |

Measured on this machine with `--model sonnet`. Persistent mode restarts itself every
`max_turns` questions so context (and latency) stay bounded.

## Install

```bash
git clone <this repo> ~/git/gamepilot && cd ~/git/gamepilot
./scripts/install-deps.sh          # pacman deps + adds you to the `input` group
uv sync
./scripts/fetch-voice.sh           # piper voice (en_US-amy-medium)
./scripts/install-kwin-rule.sh     # keeps the overlay above the game (KWin)

mkdir -p ~/.config/gamepilot
cp config/config.example.toml ~/.config/gamepilot/config.toml
cp config/mcp.example.json    ~/.config/gamepilot/mcp.json
```

Then `make deck-install` and restart OpenDeck. Nothing here needs a group change or a
relogin — that is only for the optional keyboard hotkeys.

## Use

```bash
uv run gamepilot run                      # start the daemon
uv run gamepilot run --mode oneshot       # override the backend mode
uv run gamepilot ask "best price for laranite right now"   # typed test, no mic
uv run gamepilot keys                     # find evdev names for your keys
uv run gamepilot devices                  # list input devices
```

Autostart: `cp scripts/gamepilot.service ~/.config/systemd/user/ && systemctl --user enable --now gamepilot`.

## Triggering

Deck keys and `gamepilot ctl` are the default and need no special permissions — see
[Stream Deck / OpenDeck](#stream-deck--opendeck) below.

**Keyboard / HOTAS hotkeys are off by default.** Turn them on with `[hotkeys] enabled =
true` (or `gamepilot run --hotkeys`) and gamepilot reads `/dev/input` directly, which
needs membership of the `input` group:

| key | action |
|---|---|
| `KEY_F13` | hold to talk — answer spoken to you only |
| `KEY_F14` | hold to talk **+** screenshot the game window on key-down |
| `KEY_F16` | hold to talk — answer spoken to you **and** out over voice comms |
| `KEY_F15` | cancel speech / hide overlay |

Your VIRPIL sticks are evdev devices too, so `ask_voice = "BTN_TRIGGER_HAPPY5"` binds
push-to-talk to a HOTAS button. `/dev/input` rather than a KDE shortcut, because KDE
shortcuts fire only on press (no hold-to-talk) and Wayland has no client-side global
grab. Devices are read, never grabbed, so the game still sees the keys.

## Two audio channels

Answers can be spoken to more than one destination. Each channel is a PipeWire sink
plus the `application.name` the stream carries:

| channel | target | who hears it |
|---|---|---|
| `me` | `default` (or `openwave_personal_mix`) | you |
| `squad` | `openwave_chat_mix` | everyone on voice comms — Discord captures Monitor of OpenWave Chat Mix |

`[tts.routes]` decides which hotkey speaks where, so private questions stay private and
`KEY_F16` answers out loud to the group. Give each channel its own `app_name` and
OpenWave can bind it to its own matrix row, with independent levels per mix. A channel
can also carry its own `voice_model`, so the broadcast voice is audibly not yours.

```bash
uv run gamepilot channels                       # config + the sinks that exist
uv run gamepilot say "comms check" --channel squad
uv run gamepilot ask --broadcast "eta to microtech"
```

Details in [docs/audio-channels.md](docs/audio-channels.md).

## Tray and settings

`gamepilot run` sits in the system tray — colour and tooltip show idle / listening /
working, and the menu has mute, stop speaking, new context and settings.
`gamepilot settings` opens the window on its own.

The settings window picks the **microphone** (with a 3-second test that shows the level
and what Whisper heard), configures each **output channel** (sink, volume,
`application.name`, per-channel test button), and holds the **route grid** deciding
which hotkey speaks on which channel.

It also picks the **voice** — per channel, with a preview button, and a browser that
downloads any voice from piper's published catalogue.

It writes to `config.local.toml`, which layers on top of `config.toml` — so the UI never
rewrites the file you hand-edited, comments included. Details in
[docs/ui.md](docs/ui.md).

## Stream Deck / OpenDeck

`make deck-install` installs `dev.gamepilot.sdPlugin`. Five actions: **Hold to Ask**
(keyDown records, keyUp sends — with a per-key route: private, with-screenshot, or to
the squad), **Preset Question** (text typed in the inspector, no mic), **Mute Speech**,
**Cancel**, and **Status**. A deck key needs no `input` group and cannot clash with a
game binding, which makes it the easiest trigger to live with.

It drives the daemon's control socket, and so can anything else:

```bash
gamepilot ctl press ask_voice        # a macro system can hold-to-talk too
gamepilot ctl release ask_voice
gamepilot ctl ask --route ask_broadcast "eta to microtech"
gamepilot ctl status --json
```

Details in [docs/deck.md](docs/deck.md).

## Constraints

- **Wayland + KDE Plasma 6.** The overlay needs the game in *borderless window* mode;
  a true fullscreen surface will cover it. Voice and TTS work regardless.
- **Screenshots cost tokens.** A 1080p frame is ~2.8k input tokens; gamepilot
  downscales to 1280px wide (~1.2k) before sending. Turn it off in `[screen]` if you
  only want voice.
- **`--restricted`** is on by default: Claude gets the game's MCP tools and `Read`,
  and no shell or code execution.

## Layout

```
src/gamepilot/
  backends/claude_cli.py   both CLI drivers (oneshot + persistent) and the argv builder
  capture/audio.py         push-to-talk recorder
  capture/screen.py        spectacle grab + ffmpeg downscale
  stt.py                   faster-whisper (CUDA, falls back to CPU)
  tts.py                   piper, sentence-by-sentence, fanned out to N audio channels
  overlay.py               frameless Qt overlay
  ui/tray.py               tray icon, status colour, quick actions
  ui/settings.py           mic picker, channel routing, backend options
  ui/app.py                Qt entry point and the single event dispatcher
  ipc.py                   control socket (deck keys, `gamepilot ctl`, macros)
  voices.py                installed voices, piper's catalogue, downloads
dev.gamepilot.sdPlugin/    OpenDeck / Stream Deck plugin
  hotkeys.py               evdev listener
  pipeline.py              wires it together
```
