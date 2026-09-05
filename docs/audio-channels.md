---
title: Audio channels
tags: [audio, openwave, pipewire]
updated: 2026-09-05
---

# Audio channels

A **channel** is one destination for a spoken answer: a PipeWire sink plus the
`application.name` its stream carries. Answers can go to several at once, so the same
answer reaches your ears and your squad's.

```toml
[[tts.channels]]
name = "me"
target = "default"              # what you are listening on
app_name = "gamepilot"

[[tts.channels]]
name = "squad"
target = "openwave_chat_mix"    # Discord captures Monitor of OpenWave Chat Mix
app_name = "gamepilot-squad"
volume = 0.9

[tts.routes]
ask_voice     = ["me"]
ask_screen    = ["me"]
ask_broadcast = ["me", "squad"]
```

`routes` maps a hotkey action to its channels, so `KEY_F13` answers privately and
`KEY_F16` answers to everyone. `gamepilot channels` prints the configuration next to
the sinks that actually exist; `gamepilot say hello --channel squad` proves the route
before you rely on it in a fight.

## How it reaches Discord

OpenWave publishes the Chat Mix as a capture source, and voice apps select **Monitor of
OpenWave Chat Mix** as their microphone. Anything played into `openwave_chat_mix`
therefore goes out over voice comms — gamepilot just plays there, no bot and no bridge.

Two ways to wire it, both only a `target` change:

1. **Straight at the mix** (no OpenWave setup): `target = "openwave_chat_mix"`.
2. **As a matrix row** (per-mix levels, mutes): give each channel its own
   `app_name`, then add an *app source* in OpenWave bound to that name. Each channel
   becomes its own row, so the squad channel can sit at a different level in Chat Mix
   than in Personal Mix, and can be muted independently.

Verified here with both channels speaking at once: `gamepilot` on the default sink and
`gamepilot-squad` on `openwave_chat_mix`, as two simultaneous sink-inputs.

## Playback mechanics

Each sentence is synthesised once per distinct voice, then the same PCM is written to
one `pw-play` process per channel, so the destinations stay in sync. `pw-play` is what
makes per-stream routing possible; `player = "aplay"` still works but can only reach
the default sink. A channel may set its own `voice_model`, which is a cheap way to make
the broadcast voice recognisably different from your private one.

See [[architecture]] for where speech sits in the pipeline.
