"""TOML config loading with per-game profiles."""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "gamehail" / "config.toml"


def _expand(p: str | None) -> Path | None:
    return Path(p).expanduser() if p else None


@dataclass
class BackendConfig:
    mode: str = "persistent"  # "persistent" | "oneshot"
    model: str = "sonnet"
    mcp_config: Path | None = None
    system_prompt: str = (
        "You are a live in-game assistant. The user is playing and cannot read long text. "
        "Answer in at most 3 short sentences, numbers first, no preamble, no markdown. "
        "Use the game MCP tools for any factual lookup instead of guessing."
    )
    allowed_tools: list[str] = field(default_factory=lambda: ["Read"])
    permission_mode: str = "dontAsk"
    restricted: bool = True
    effort: str | None = "low"
    add_dirs: list[Path] = field(default_factory=list)
    timeout_s: float = 120.0
    # Restart the persistent session after this many turns. Bounds context growth, but
    # also matters for behaviour: a long-lived session's own prior turns bleed into
    # later ones (a run of "nothing found" answers made the model give up faster on the
    # next question, even with an explicit instruction not to - confirmed by resetting
    # mid-session and getting the right answer immediately after). Kept low now that a
    # reset's cost is mostly hidden - see Pipeline.reset_backend().
    max_turns: int = 15


@dataclass
class HotkeyConfig:
    # Off by default: the deck plugin and the control socket cover triggering without
    # needing membership of the `input` group, and without risking a key the game owns.
    # Turn this on to read /dev/input directly for hold-to-talk on a key or HOTAS button.
    enabled: bool = False
    # Held = push-to-talk. Tapped = screenshot question (voice follows if held).
    ask_voice: str = "KEY_F13"
    ask_screen: str = "KEY_F14"
    ask_broadcast: str = "KEY_F16"
    cancel: str = "KEY_F15"
    devices: list[str] = field(default_factory=list)  # empty = autodetect
    min_hold_ms: int = 150


@dataclass
class SttConfig:
    input_device: str = ""  # PortAudio device name (empty = system default)
    model: str = "small.en"
    device: str = "auto"  # auto | cuda | cpu
    compute_type: str = "auto"
    language: str = "en"
    samplerate: int = 16000
    max_seconds: float = 30.0
    # Words Whisper has never met - ship names, commodities, systems. Passed as the
    # decoder's initial prompt, which biases it towards spelling them the game's way
    # instead of the nearest English phrase ("lara night" for laranite).
    vocabulary: list[str] = field(default_factory=list)


@dataclass
class TtsChannel:
    """One audio destination for spoken answers.

    `target` is a PipeWire sink node name ("default" for whatever you are listening
    to); `app_name` is the application.name the stream carries, which is what
    OpenWave binds an app source row to.
    """

    name: str = "me"
    target: str = "default"
    app_name: str = "gamehail"
    volume: float = 1.0
    voice_model: Path | None = None  # overrides TtsConfig.voice_model
    enabled: bool = True


def _default_channels() -> list[TtsChannel]:
    return [
        TtsChannel(name="me", target="default", app_name="gamehail"),
        TtsChannel(
            name="squad", target="openwave_chat_mix", app_name="gamehail-squad",
            enabled=False,
        ),
    ]


def _default_routes() -> dict[str, list[str]]:
    return {
        "ask_voice": ["me"],
        "ask_screen": ["me"],
        "ask_broadcast": ["me", "squad"],
    }


@dataclass
class TtsConfig:
    enabled: bool = True
    voice_model: Path | None = None  # path to a piper .onnx (per-channel overridable)
    speaker: int | None = None
    length_scale: float | None = None
    player: str = "pw-play"  # "pw-play" (routable) or "aplay" (default sink only)
    # Ceiling applied to every sentence before playback. piper renders at full scale,
    # which clips against game audio already in the mix.
    peak: float = 0.5
    channels: list[TtsChannel] = field(default_factory=_default_channels)
    routes: dict[str, list[str]] = field(default_factory=_default_routes)


@dataclass
class ScreenConfig:
    enabled: bool = True
    mode: str = "active"  # active | full
    max_width: int = 1280
    quality: int = 4  # ffmpeg -q:v, lower is better quality


@dataclass
class OverlayConfig:
    enabled: bool = True
    width: int = 620
    corner: str = "top-right"  # top-right | top-left | bottom-right | bottom-left
    margin: int = 24
    font_size: int = 15
    opacity: float = 0.92
    hide_after_s: float = 20.0


@dataclass
class GamesConfig:
    """Which game module answers. Modules live in `gamemodules.py`."""

    auto_switch: bool = True   # follow whichever game is running
    default: str = "generic"   # when nothing is detected
    override: str = ""         # pin one module regardless of what is running


@dataclass
class UiConfig:
    tray: bool = True
    show_answers_in_tray: bool = True


@dataclass
class Config:
    path: Path | None = None
    profile: str = "default"
    backend: BackendConfig = field(default_factory=BackendConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    games: GamesConfig = field(default_factory=GamesConfig)
    work_dir: Path = Path("/tmp/gamehail")


def _merge(base: Any, patch: dict[str, Any]) -> Any:
    """Return a copy of a dataclass with keys from `patch` applied."""
    known = {f.name for f in base.__dataclass_fields__.values()}
    unknown = set(patch) - known
    if unknown:
        raise ValueError(f"unknown keys for {type(base).__name__}: {sorted(unknown)}")
    return replace(base, **patch)


def _coerce(section: str, data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if section == "backend":
        if "mcp_config" in out:
            out["mcp_config"] = _expand(out["mcp_config"])
        if "add_dirs" in out:
            out["add_dirs"] = [Path(p).expanduser() for p in out["add_dirs"]]
    if section == "tts":
        if "voice_model" in out:
            out["voice_model"] = _expand(out["voice_model"])
        if "channels" in out:
            out["channels"] = [
                TtsChannel(**{**ch, "voice_model": _expand(ch.get("voice_model"))})
                for ch in out["channels"]
            ]
        if "routes" in out:
            out["routes"] = {k: list(v) for k, v in out["routes"].items()}
    return out


def local_path_for(path: Path) -> Path:
    """UI-managed settings live beside the config as `<name>.local.toml`.

    Writing them separately keeps the hand-written file - and its comments - intact,
    since a TOML round-trip through a writer would drop every comment in it.
    """
    return path.with_name(f"{path.stem}.local{path.suffix}")


def _profile_layer(raw: dict[str, Any], profile: str) -> dict[str, Any]:
    table = raw.get("profiles", {})
    if isinstance(table, dict) and isinstance(table.get(profile), dict):
        return table[profile]
    return {}


def load(path: Path | None = None, profile: str | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = tomllib.loads(path.read_text())

    local_path = local_path_for(path)
    local: dict[str, Any] = {}
    if local_path.is_file():
        local = tomllib.loads(local_path.read_text())

    profile = profile or local.get("profile") or raw.get("profile") or "default"
    # Later layers win: file, its profile, then the UI-managed overlay and its profile.
    layers = [
        raw,
        _profile_layer(raw, profile),
        local,
        _profile_layer(local, profile),
    ]

    cfg = Config(path=path, profile=profile)
    for layer in layers:
        for section in ("backend", "hotkeys", "stt", "tts", "screen", "overlay", "ui",
                        "games"):
            if section in layer:
                setattr(cfg, section, _merge(getattr(cfg, section), _coerce(section, layer[section])))
        if "work_dir" in layer:
            cfg.work_dir = Path(layer["work_dir"]).expanduser()

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    if cfg.work_dir not in cfg.backend.add_dirs:
        cfg.backend.add_dirs.append(cfg.work_dir)
    return cfg


# -- writing back ----------------------------------------------------------------


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def save_patch(patch: dict[str, Any], path: Path | None = None) -> Path:
    """Merge `patch` into the UI-managed overlay file, leaving the main config alone.

    The overlay is loaded after the main file and after its profile table, so what the
    settings window writes is what takes effect.
    """
    import tomli_w

    path = local_path_for(path or DEFAULT_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = tomllib.loads(path.read_text()) if path.is_file() else {}
    merged = _deep_merge(current, patch)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(tomli_w.dumps(merged))
    tmp.replace(path)
    return path
