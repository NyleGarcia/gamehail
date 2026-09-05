---
title: Now
tags: [plans]
updated: 2026-09-05
---

# Now

Verified on this machine: faster-whisper `small.en` runs on CUDA/float16 (RTX 5080), 8.9s cold load.

- [ ] Place the deck keys in OpenDeck and set each one's route
- [x] Regenerate vocabulary from SCMCP's sc_get_vocabulary (658 generated + 52 hand-picked terms: commodities, manufacturers, weapons, attachments, ship components)
- [ ] "Agricium" still transcribes as "Agrisium" even with vocabulary biasing - try `medium.en` if it recurs
- [x] Stop hardcoding a local SCMCP path in the shipped module; make gateway vs local a user choice
- [ ] Wire scripts/vocab/<id>.py for the placeholder games once each gets an MCP server
- [ ] Confirm the KWin rule keeps the overlay above Star Citizen in borderless mode
- [ ] Decide whether `me` should target `openwave_personal_mix` explicitly rather than the default sink
- [ ] Add the gamehail / gamehail-squad app sources in OpenWave to get per-mix levels
- [ ] Rebind hotkeys from the settings window instead of the config file
- [ ] Tune the Star Citizen module's prompt against real in-flight questions
- [x] Write more game modules — World of Warcraft, Elite Dangerous, Minecraft, Elden Ring (placeholders, no MCP yet)
