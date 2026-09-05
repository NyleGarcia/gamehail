"""TOML config loading with per-game profiles."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "gamepilot" / "config.toml"


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
    # Restart the persistent session after this many turns to bound context growth.
    max_turns: int = 40


@dataclass
class HotkeyConfig:
    # Held = push-to-talk. Tapped = screenshot question (voice follows if held).
    ask_voice: str = "KEY_F13"
    ask_screen: str = "KEY_F14"
    cancel: str = "KEY_F15"
    devices: list[str] = field(default_factory=list)  # empty = autodetect
    min_hold_ms: int = 150


@dataclass
class SttConfig:
    model: str = "small.en"
    device: str = "auto"  # auto | cuda | cpu
    compute_type: str = "auto"
    language: str = "en"
    samplerate: int = 16000
    max_seconds: float = 30.0


@dataclass
class TtsConfig:
    enabled: bool = True
    voice_model: Path | None = None  # path to a piper .onnx
    speaker: int | None = None
    length_scale: float | None = None
    player: list[str] = field(
        default_factory=lambda: ["aplay", "-q", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-c", "1"]
    )


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
class Config:
    profile: str = "default"
    backend: BackendConfig = field(default_factory=BackendConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    work_dir: Path = Path("/tmp/gamepilot")


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
    if section == "tts" and "voice_model" in out:
        out["voice_model"] = _expand(out["voice_model"])
    return out


def load(path: Path | None = None, profile: str | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = tomllib.loads(path.read_text())

    profile = profile or raw.get("profile") or "default"
    layers = [raw]
    prof_table = raw.get("profile_", raw.get("profiles", {}))
    if isinstance(prof_table, dict) and profile in prof_table:
        layers.append(prof_table[profile])

    cfg = Config(profile=profile)
    for layer in layers:
        for section in ("backend", "hotkeys", "stt", "tts", "screen", "overlay"):
            if section in layer:
                setattr(cfg, section, _merge(getattr(cfg, section), _coerce(section, layer[section])))
        if "work_dir" in layer:
            cfg.work_dir = Path(layer["work_dir"]).expanduser()

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    if cfg.work_dir not in cfg.backend.add_dirs:
        cfg.backend.add_dirs.append(cfg.work_dir)
    return cfg
