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
app_name = "gamehail"

[[tts.channels]]
name = "squad"
target = "openwave_chat_mix"    # Discord captures Monitor of OpenWave Chat Mix
app_name = "gamehail-squad"
volume = 0.9

[tts.routes]
ask_voice     = ["me"]
ask_screen    = ["me"]
ask_broadcast = ["me", "squad"]
```

`routes` maps a hotkey action to its channels, so `KEY_F13` answers privately and
`KEY_F16` answers to everyone. `gamehail channels` prints the configuration next to
the sinks that actually exist; `gamehail say hello --channel squad` proves the route
before you rely on it in a fight.

## How it reaches Discord

OpenWave publishes the Chat Mix as a capture source, and voice apps select **Monitor of
OpenWave Chat Mix** as their microphone. Anything played into `openwave_chat_mix`
therefore goes out over voice comms — gamehail just plays there, no bot and no bridge.

Two ways to wire it, both only a `target` change:

1. **Straight at the mix** (no OpenWave setup): `target = "openwave_chat_mix"`.
2. **As a matrix row** (per-mix levels, mutes): give each channel its own
   `app_name`, then add an *app source* in OpenWave bound to that name. Each channel
   becomes its own row, so the squad channel can sit at a different level in Chat Mix
   than in Personal Mix, and can be muted independently.

Verified here with both channels speaking at once: `gamehail` on the default sink and
`gamehail-squad` on `openwave_chat_mix`, as two simultaneous sink-inputs.

## Aim at a mix, not at "default"

Measured at the headphones with the same sentence, played three ways:

| routing | peak |
|---|---|
| default sink, `--media-role Notification` | 0.048 |
| default sink, no role | 0.084 |
| `--target openwave_personal_mix` | 1.000 |

Going through the default sink means going through whichever OpenWave *source* row it
happens to be (here `openwave_src_system`), and that row's trim and send left the answer
~21 dB down — audible in a quiet room, gone under a game. The Notification media role
costs another ~5 dB on top, because WirePlumber's role policy ducks it. So channels
target a mix sink directly and set no media role.

If a channel ever goes quiet again, record the monitor and measure rather than guess:

```bash
parec --device=openwave_personal_mix.monitor --format=s16le --rate=48000 --channels=2 \
  > /tmp/probe.raw &
gamehail ctl ask "say exactly: audio check"
```

## Playback mechanics

Each sentence is synthesised once per distinct voice, then the same PCM is written to
one `pw-play` process per channel, so the destinations stay in sync. `pw-play` is what
makes per-stream routing possible; `player = "aplay"` still works but can only reach
the default sink. A channel may set its own `voice_model`, which is a cheap way to make
the broadcast voice recognisably different from your private one.

## Choosing the voice

`tts.voice_model` is the default voice and any channel may override it, which is the
cheap way to make the squad's copy audibly not you. The settings window lists what is
installed, previews a voice before you commit to it, and **Get more voices…** browses
piper's published catalogue (its own `voices.json`, so the list is what actually exists)
and downloads on a worker thread. `tts.length_scale` is the pace — below 1.0 is faster.

See [[architecture]] for where speech sits in the pipeline, and [[deck]] for triggering
an answer from a deck key.
