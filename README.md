# gamehail

Voice assistant for games on Linux. Hold a key, ask a question out loud, get a spoken
answer plus an on-screen overlay — answered by Claude Code running headless against a
game's MCP server (e.g. [SCMCP](https://github.com/) for Star Citizen).

No API key, no per-token bill: it drives the `claude` CLI, so it runs on your existing
Claude Code subscription.

```
  hold a deck key ─┐
  gamehail ctl ───┼─► mic ──► whisper ──► claude -p (+ game MCP) ─┬─► piper ──► "me"    (your headset)
  (keyboard: opt-in)│      + screenshot ──┘                        ├─► piper ──► "squad" (OpenWave Chat Mix ──► Discord)
                    │                                              └─► Qt overlay + tray
```

## One module per game

gamehail is not tied to a single game. A **module** is a TOML file naming the MCP
servers that know a game, the words the speech recogniser has never met, and how to
answer — and gamehail switches to it automatically when that game is running.

```bash
gamehail games                    # what is installed, what is running, what is active
gamehail --game star-citizen ask "best price for laranite"
```

**Star Citizen ships fully wired** ([SCMCP](https://github.com/) for live prices,
terminals, trade routes and ships, plus 52 vocabulary terms so Whisper stops hearing
"lara night"). World of Warcraft, Elite Dangerous, Minecraft and Elden Ring ship as
placeholders — detection and vocabulary done, no MCP server wired in yet, so they
answer from the model alone until one exists. Adding a game, or finishing one of
these, is adding or editing a file in `~/.config/gamehail/games/` — see
[docs/games.md](docs/games.md).

## Two backend modes

| mode | what it does | first answer | follow-ups | context |
|---|---|---|---|---|
| `persistent` (default) | one long-lived `claude` process fed over stdin | ~3s | **~1s** (warm prompt cache) | carries over between questions |
| `oneshot` | fresh `claude -p` per question | ~3s | ~3s | none |

Measured on this machine with `--model sonnet`. Persistent mode restarts itself every
`max_turns` questions so context (and latency) stay bounded.

## Install

```bash
git clone <this repo> ~/git/gamehail && cd ~/git/gamehail
./scripts/install-deps.sh          # pacman deps + adds you to the `input` group
uv sync
./scripts/fetch-voice.sh           # piper voice (en_US-amy-medium)
./scripts/install-kwin-rule.sh     # keeps the overlay above the game (KWin)

mkdir -p ~/.config/gamehail/games
cp config/config.example.toml ~/.config/gamehail/config.toml
```

Each game module says which MCP server it needs but not where it runs - that choice is
yours. Copy the module you want (`src/gamehail/games/star-citizen.toml`) into
`~/.config/gamehail/games/`, and add an `[mcp.scmcp]` block: a gateway URL if you run
SCMCP hosted somewhere, or a local command if you run it as a stdio checkout. Both
shapes are documented at the bottom of the module file itself.

Then `make deck-install` and restart OpenDeck. Nothing here needs a group change or a
relogin — that is only for the optional keyboard hotkeys.

## Use

```bash
uv run gamehail run                      # start the daemon
uv run gamehail run --mode oneshot       # override the backend mode
uv run gamehail ask "best price for laranite right now"   # typed test, no mic
uv run gamehail keys                     # find evdev names for your keys
uv run gamehail devices                  # list input devices
```

Autostart:

```bash
cp scripts/gamehail.service ~/.config/systemd/user/
systemctl --user enable --now gamehail
journalctl --user -u gamehail -f      # what it heard, what it answered
```

## Triggering

Deck keys and `gamehail ctl` are the default and need no special permissions — see
[Stream Deck / OpenDeck](#stream-deck--opendeck) below.

**Keyboard / HOTAS hotkeys are off by default.** Turn them on with `[hotkeys] enabled =
true` (or `gamehail run --hotkeys`) and gamehail reads `/dev/input` directly, which
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
| `me` | `openwave_personal_mix` | you |
| `squad` | `openwave_chat_mix` | everyone on voice comms — Discord captures Monitor of OpenWave Chat Mix |

Aim a channel at a **mix**, not at `default`: the default sink is an OpenWave *source*
row, and its trim and send left answers ~21 dB down at the headphones.

`[tts.routes]` decides which hotkey speaks where, so private questions stay private and
`KEY_F16` answers out loud to the group. Give each channel its own `app_name` and
OpenWave can bind it to its own matrix row, with independent levels per mix. A channel
can also carry its own `voice_model`, so the broadcast voice is audibly not yours.

```bash
uv run gamehail channels                       # config + the sinks that exist
uv run gamehail say "comms check" --channel squad
uv run gamehail ask --broadcast "eta to microtech"
```

Details in [docs/audio-channels.md](docs/audio-channels.md).

## Tray and settings

`gamehail run` sits in the system tray — colour and tooltip show idle / listening /
working, and the menu has mute, stop speaking, new context and settings.
`gamehail settings` opens the window on its own.

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

`make deck-install` installs `dev.gamehail.sdPlugin`. Five actions: **Hold to Ask**
(keyDown records, keyUp sends — with a per-key route: private, with-screenshot, or to
the squad), **Preset Question** (text typed in the inspector, no mic), **Mute Speech**,
**Cancel**, and **Status**. A deck key needs no `input` group and cannot clash with a
game binding, which makes it the easiest trigger to live with.

It drives the daemon's control socket, and so can anything else:

```bash
gamehail ctl press ask_voice        # a macro system can hold-to-talk too
gamehail ctl release ask_voice
gamehail ctl ask --route ask_broadcast "eta to microtech"
gamehail ctl status --json
```

Details in [docs/deck.md](docs/deck.md).

## When it says "heard nothing"

The answer path is: record → whisper → claude → speech. If a question goes nowhere,
`journalctl --user -u gamehail -n 30` names the step that failed. A "heard nothing"
that also reports a mic level near zero means the wrong input device, which the settings
window's mic test settles in three seconds.

Speech recognition uses the GPU when it can (measured here: 0.64s for a 3.3s question on
an RTX 5080) and drops to CPU on its own if the CUDA libraries are unusable.

## Constraints

- **Wayland + KDE Plasma 6.** The overlay needs the game in *borderless window* mode;
  a true fullscreen surface will cover it. Voice and TTS work regardless.
- **Screenshots cost tokens.** A 1080p frame is ~2.8k input tokens; gamehail
  downscales to 1280px wide (~1.2k) before sending. Turn it off in `[screen]` if you
  only want voice.
- **`--restricted`** is on by default: Claude gets the game's MCP tools and `Read`,
  and no shell or code execution.

## Layout

```
src/gamehail/
  backends/claude_cli.py   both CLI drivers (oneshot + persistent) and the argv builder
  capture/audio.py         push-to-talk recorder
  capture/screen.py        spectacle grab + ffmpeg downscale
  stt.py                   faster-whisper (CUDA, falls back to CPU)
  tts.py                   piper, sentence-by-sentence, fanned out to N audio channels
  overlay.py               frameless Qt overlay
  ui/tray.py               tray icon, status colour, quick actions
  ui/settings.py           mic picker, channel routing, backend options
  ui/app.py                Qt entry point and the single event dispatcher
  ipc.py                   control socket (deck keys, `gamehail ctl`, macros)
  voices.py                installed voices, piper's catalogue, downloads
dev.gamehail.sdPlugin/    OpenDeck / Stream Deck plugin
  hotkeys.py               evdev listener
  pipeline.py              wires it together
```
