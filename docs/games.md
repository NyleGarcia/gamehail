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

## Adding one

```bash
mkdir -p ~/.config/gamehail/games
cp path/to/example.toml ~/.config/gamehail/games/my-game.toml
gamehail games                       # lists modules, marks running and active
gamehail --game my-game ask "…"      # try it before relying on detection
```

See [[architecture]] for where the module sits in the pipeline.
