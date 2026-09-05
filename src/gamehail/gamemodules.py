"""Game modules: one file per game, describing how to help with it.

A module is data, not code - a TOML file naming the MCP servers that know the game, the
words the speech recogniser has never met, and how the assistant should answer. Adding a
game means adding a file, and the modules that ship with gamehail are found the same way
as the ones you write.

Search order (later wins on the same id):

1. `src/gamehail/games/*.toml` - shipped with gamehail
2. `~/.config/gamehail/games/*.toml` - yours

Detection is by running process, so switching games switches the assistant with no
keypress. `[games] auto_switch = false` pins whatever `default` names instead.
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BUNDLED_DIR = Path(__file__).resolve().parent / "games"


def user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(base) / "gamehail" / "games"


@dataclass
class GameModule:
    id: str
    name: str
    # Substrings matched against running process names; first hit wins.
    detect: list[str] = field(default_factory=list)
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    # Words Whisper has never met. Two sources feed the one list actually used:
    # `vocabulary` (or `vocabulary_static`) is gamedata - hand-picked, ships in the repo,
    # works with no network and no API key. `vocabulary_generated` is filled by a
    # scripts/vocab/<id>.py builder that hits the game's own API (UEX/SCW for Star
    # Citizen) when one is reachable; when it is not, whatever was last generated stays
    # in the file, so gamedata is always the fallback - there is no runtime dependency
    # on the API being up.
    vocabulary: list[str] = field(default_factory=list)
    mcp: dict[str, Any] = field(default_factory=dict)
    mcp_config: Path | None = None  # an existing mcp.json, instead of inline [mcp.*]
    model: str | None = None
    effort: str | None = None
    source: Path | None = None

    @classmethod
    def from_toml(cls, path: Path) -> "GameModule":
        data = tomllib.loads(path.read_text())
        game = data.get("game", data)
        mcp_config = game.get("mcp_config")
        # Order preserved, duplicates dropped: a term in both stays where gamedata put
        # it, so a hand-picked entry's position (and any comment above it) is stable
        # across regenerations of the generated half.
        seen: dict[str, None] = {}
        for term in (*game.get("vocabulary", []), *game.get("vocabulary_static", []),
                    *game.get("vocabulary_generated", [])):
            seen.setdefault(term, None)
        return cls(
            id=game.get("id") or path.stem,
            name=game.get("name") or path.stem,
            detect=list(game.get("detect", [])),
            system_prompt=game.get("system_prompt", ""),
            allowed_tools=list(game.get("allowed_tools", [])),
            vocabulary=list(seen),
            mcp=data.get("mcp", {}),
            mcp_config=Path(os.path.expandvars(mcp_config)).expanduser() if mcp_config else None,
            model=game.get("model"),
            effort=game.get("effort"),
            source=path,
        )

    def mcp_config_path(self, work_dir: Path) -> Path | None:
        """Where the `claude` CLI should read this module's MCP servers from.

        Inline `[mcp.*]` tables are written out as a real mcp.json, so a module stays a
        single self-contained file while the CLI still gets the format it expects.
        """
        if self.mcp_config:
            return self.mcp_config
        if not self.mcp:
            return None
        servers = {}
        for name, entry in self.mcp.items():
            server = dict(entry)
            for key in ("command",):
                if key in server:
                    server[key] = os.path.expandvars(str(server[key]))
            if "args" in server:
                server["args"] = [os.path.expandvars(str(a)) for a in server["args"]]
            servers[name] = server
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / f"mcp-{self.id}.json"
        out.write_text(json.dumps({"mcpServers": servers}, indent=2))
        return out


def discover() -> dict[str, GameModule]:
    modules: dict[str, GameModule] = {}
    for directory in (BUNDLED_DIR, user_dir()):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.toml")):
            try:
                module = GameModule.from_toml(path)
            except (OSError, tomllib.TOMLDecodeError, TypeError) as exc:
                log.error("ignoring game module %s: %s", path, exc)
                continue
            modules[module.id] = module
    return modules


def running_processes() -> list[str]:
    names = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            names.append((entry / "comm").read_text().strip())
        except OSError:
            continue  # the process ended between listing and reading
    return names


def detect(modules: dict[str, GameModule] | None = None,
           processes: list[str] | None = None) -> GameModule | None:
    """The module whose game is running, if any."""
    modules = discover() if modules is None else modules
    processes = running_processes() if processes is None else processes
    lowered = [p.lower() for p in processes]
    for module in modules.values():
        for needle in module.detect:
            needle = needle.lower()
            if any(needle in name for name in lowered):
                return module
    return None


def resolve(default_id: str = "generic", auto_switch: bool = True,
            override: str | None = None) -> GameModule | None:
    """Which module to answer as: an explicit choice, then detection, then the default."""
    modules = discover()
    if override:
        if override in modules:
            return modules[override]
        log.error("unknown game module %r; known: %s", override, sorted(modules))
    if auto_switch and (found := detect(modules)):
        return found
    return modules.get(default_id)
