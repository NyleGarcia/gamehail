# gamepilot

Hotkey gaming assistant for Linux. Hold a key, ask a question out loud, get a spoken
answer plus an on-screen overlay — answered by Claude Code running headless against a
game's MCP server (e.g. [SCMCP](https://github.com/) for Star Citizen).

No API key, no per-token bill: it drives the `claude` CLI, so it runs on your existing
Claude Code subscription.

```
  hold KEY_F13 ──► mic ──► whisper ──► claude -p (+ game MCP) ──► piper TTS
  tap  KEY_F14 ──► screenshot ──┘                            └─► Qt overlay
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

Log out and back in once, so the `input` group membership takes effect.

## Use

```bash
uv run gamepilot run                      # start the daemon
uv run gamepilot run --mode oneshot       # override the backend mode
uv run gamepilot ask "best price for laranite right now"   # typed test, no mic
uv run gamepilot keys                     # find evdev names for your keys
uv run gamepilot devices                  # list input devices
```

Autostart: `cp scripts/gamepilot.service ~/.config/systemd/user/ && systemctl --user enable --now gamepilot`.

## Default keys

| key | action |
|---|---|
| `KEY_F13` | hold to talk |
| `KEY_F14` | hold to talk **+** screenshot the game window on key-down |
| `KEY_F15` | cancel speech / hide overlay |

Hotkeys are read from `/dev/input` rather than registered with KDE, because KDE
shortcuts fire only on press (no hold-to-talk) and Wayland has no client-side global
grab. The devices are read, never grabbed, so the game still sees the keys — bind
gamepilot to keys the game does not use.

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
  tts.py                   piper, speaks sentence-by-sentence while the answer streams
  overlay.py               frameless Qt overlay
  hotkeys.py               evdev listener
  pipeline.py              wires it together
```
