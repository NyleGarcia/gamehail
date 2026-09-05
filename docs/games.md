---
title: Game modules
tags: [games, mcp, modules]
updated: 2026-09-05
---

# Game modules

gamehail is not built around one game. A **module** is a TOML file describing how to
help with a particular game — the MCP servers that know it, the words the speech
recogniser has never met, and how the assistant should answer. Adding a game is adding
a file; no code changes.

```
src/gamehail/games/*.toml        shipped with gamehail
~/.config/gamehail/games/*.toml  yours — wins on a matching id
```

## Installed today

| module | MCP server | status |
|---|---|---|
| `star-citizen` | [SCMCP](https://github.com/) — live prices, terminals, trade routes, ships | wired up |
| `world-of-warcraft` | none yet | placeholder: detection + vocabulary only |
| `elite-dangerous` | none yet | placeholder: detection + vocabulary only |
| `minecraft` | none yet | placeholder: detection + vocabulary only |
| `elden-ring` | none yet | placeholder: detection + vocabulary only |
| `generic` | none | fallback when nothing is detected |

A placeholder still answers from the model alone and gets detection and vocabulary
right, which is most of the work of adding a game — wiring in an MCP server is the part
left undone. `elite-dangerous` in particular is a short step away: the game's own
Journal file is line-delimited JSON, sitting under the Proton prefix's `Saved Games`,
and reading it live for current system, dock state and cargo is enough for a first cut.

Star Citizen is the first fully-wired module, and the shape every other one follows:

```toml
[game]
id = "star-citizen"
name = "Star Citizen"
detect = ["StarCitizen.exe", "RSI Launcher"]   # matched against running processes
allowed_tools = ["Read", "mcp__scmcp"]
system_prompt = """Answer in at most 3 short sentences…"""
vocabulary = ["laranite", "quantanium", "microTech", …]

[mcp.scmcp]
command = "sh"
args = ["-c", ". $HOME/.secrets/scmcp.env && exec node $HOME/git/SCMCP/dist/index.js"]
```

## Where a game's MCP server runs is your choice, not the module's

A shipped module says a game needs `mcp.scmcp` in `allowed_tools` - it does not say
where that server runs. Baking a local stdio path into a module that ships in the repo
only works on the machine it was written on. Point it at your own setup in
`~/.config/gamehail/games/<id>.toml`:

```toml
# Gateway - a hosted server reachable over HTTP, nothing to run locally
[mcp.scmcp]
type = "http"
url = "https://your-gateway.example/servers/scmcp/mcp/"
```

```toml
# Local - a checkout on this machine, spoken to over stdio
[mcp.scmcp]
command = "sh"
args = ["-c", ". $HOME/.secrets/scmcp.env && exec node $HOME/git/SCMCP/dist/index.js"]
```

A user file **merges** onto the bundled module of the same id rather than replacing it
- `[game]` and `[mcp]` merge key-by-key - so adding just `[mcp.scmcp]` is enough; the
bundled `system_prompt`, `detect` and vocabulary are still inherited. Override any other
field (`model`, `effort`, ...) the same way, in the same file.

### Optional: scoping which tools SCMCP advertises

SCMCP >= 1.5.0 reads `SCMCP_TOOLS` (comma-separated tool names) and only advertises
those. This is a latency optimization, not a capability change: Claude Code searches
for a tool before its first use in a session, and that search is measurably slower
against SCMCP's full 25+ tools than a scoped handful - a real ~3s difference on a
session's first question. Set it in the same shell wrapper as the stdio command:

```bash
export SCMCP_TOOLS='uex_get_commodity_prices,uex_get_commodities,uex_get_commodity_averages,uex_get_terminals,uex_get_terminal_inventory,uex_get_trade_routes,uex_get_ship_prices,uex_search_marketplace,scw_get_item,scw_get_vehicle,scw_list_vehicles,scw_search,scw_search_ships_by_vendor,sc_get_vocabulary'
```

**Keep this list in sync with what the system prompt asks for.** It's a real footgun:
an earlier version of this list left out `uex_search_marketplace` and `scw_search`,
which silently broke marketplace listings and fuzzy name matching - the assistant
simply couldn't see those tools existed, and there was no error, just a wrong-looking
answer that took a `sc_get_vocabulary`-style investigation to trace back to a scoping
list, not a prompt or model problem. If a capability that should work doesn't,
check this list before anything else. Leave `SCMCP_TOOLS` unset to advertise
everything - correct by default, just a few seconds slower on a session's first
question.

## Switching

`[games] auto_switch` (on by default) picks the module whose `detect` list matches a
running process, so starting a game switches the assistant with no keypress. Nothing
running falls back to `[games] default`; `[games] override`, `--game`, or the Game
dropdown in settings pins one regardless.

Switching restarts the warm `claude` session, because that session was started with the
previous game's prompt and tool list. The cost is one cold answer (~3s) after a switch.

Detection reads `/proc` and is throttled to once every five seconds, so asking three
questions in a row does not rescan the process table three times.

## What a module reconfigures

| module field | what it sets |
|---|---|
| `system_prompt` | how the assistant answers, per game |
| `allowed_tools` | which MCP tools the CLI may call |
| `mcp` / `mcp_config` | the MCP servers themselves — inline tables are written out as a real `mcp.json` |
| `vocabulary` | Whisper's initial prompt, so game jargon transcribes correctly |
| `model`, `effort` | optional per-game overrides |

`$HOME` and other environment variables are expanded in `command` and `args`, so a
module stays portable between machines.

## Vocabulary: two tiers, regenerated from the game's own MCP server

`vocabulary_static` is hand-picked and always present - ships, locations, jargon that
isn't a tradeable item and so nothing can generate it. `vocabulary_generated` is filled
by `scripts/vocab/<id>.py`, which calls the game's MCP server rather than duplicating
its data-fetching logic. For Star Citizen that's `sc_get_vocabulary` (SCMCP >= 1.4.0),
itself merging locally extracted names (`SCMCP_GAME_DATA_DIR` - ore signatures,
blueprints, Wikelo trades: exactly the invented words no public API exposes) with
tradeable commodity names, manufacturer names, weapons, weapon attachments and ship
components from UEX/SCW - 710 terms total (52 hand-picked + 658 generated). Armor and
clothes are deliberately left out of gamehail's own call: ~4,100 terms, almost entirely
colour-variant SKUs ("Rating Undersuit - Sand/Black/Red/...") that dilute the prompt
far more than they help - `sc_get_vocabulary` supports them (`include_armor`,
`include_clothes`) if you want them anyway.

The precedence is local-first at two levels:

1. **The builder script** reads a checked-in snapshot
   (`src/gamehail/games/data/<id>.vocab.json`) by default - instant, no network, no
   token, ships in the repo. `--refresh` calls the MCP server instead and rewrites the
   snapshot on success; if the server can't be reached, the existing snapshot is left
   alone rather than the module losing its vocabulary.
2. **The MCP server itself** prefers local gamedata over the public API for the same
   reason - it's the richer, more game-specific source, and the API is what's always
   available as a fallback.

```bash
uv run python scripts/vocab/star-citizen.py             # snapshot, instant, offline
uv run python scripts/vocab/star-citizen.py --refresh    # re-fetch via SCMCP
SCMCP_GAME_DATA_DIR=~/git/StarBreaker/out uv run python scripts/vocab/star-citizen.py --refresh
```

Never hand-edit `vocabulary_generated` - the next `--refresh` overwrites it. Add
permanent, hand-picked terms to `vocabulary_static` instead.

## Adding one

```bash
mkdir -p ~/.config/gamehail/games
cp path/to/example.toml ~/.config/gamehail/games/my-game.toml
gamehail games                       # lists modules, marks running and active
gamehail --game my-game ask "…"      # try it before relying on detection
```

See [[architecture]] for where the module sits in the pipeline.
