# Game modules

One TOML file per game. Files here ship with gamehail; files in
`~/.config/gamehail/games/` are yours and win on a matching `id`.

```toml
[game]
id = "my-game"
name = "My Game"
detect = ["MyGame.exe"]        # matched against running process names
allowed_tools = ["Read", "mcp__mygame"]
system_prompt = """Answer in at most 3 short sentences."""
vocabulary = ["names", "the speech recogniser", "has never met"]

[mcp.mygame]                    # or: mcp_config = "~/.config/gamehail/mcp.json"
command = "node"
args = ["$HOME/src/mygame-mcp/dist/index.js"]
```

`gamehail games` lists what is installed and which one is running.
