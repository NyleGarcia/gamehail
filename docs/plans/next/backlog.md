---
title: Next
tags: [plans]
updated: 2026-09-05
---

# Next

- Wake word instead of push-to-talk (openWakeWord)
- **VoiceAttack integration.** VoiceAttack is the Windows voice-command tool a lot of
  sim/HOTAS players already have bound into their game (often via Proton). Two shapes
  worth comparing: (a) a VoiceAttack plugin (C#, VoiceAttack's plugin API) that calls
  gamehail's control socket the same way the OpenDeck plugin does — VoiceAttack owns
  the wake phrase and hold-to-talk, gamehail owns the answer; or (b) gamehail listens
  for VoiceAttack's own recognized-command events instead of running its own hotkey/STT
  path, so a phrase already spoken to VoiceAttack can also reach gamehail. (a) is
  probably simpler and keeps the two tools decoupled — VoiceAttack triggers, gamehail
  answers — rather than needing gamehail to speak VoiceAttack's format. Needs a Windows
  box (or Proton/Wine) to prototype against; nothing here yet.
- Finish an MCP server for one of the placeholder games (elite-dangerous's Journal
  files are the shortest path — line-delimited JSON under the Proton prefix)
- Detect games by focused window class as well as process name
- Answer history pane in the overlay
