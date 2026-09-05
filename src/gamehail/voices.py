"""Finding, listing and fetching piper voices.

The catalogue is piper's own `voices.json` from Hugging Face, cached on disk, so the
voice list is whatever piper actually publishes rather than a list guessed here. With
no network and no cache, only installed voices are offered.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
CATALOGUE_URL = f"{BASE}/voices.json"


def voices_dir() -> Path:
    data = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(data) / "gamehail" / "voices"


def installed() -> list[Path]:
    """Every usable voice on disk (an .onnx with its sidecar json beside it)."""
    directory = voices_dir()
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.glob("*.onnx")
        if p.with_suffix(p.suffix + ".json").is_file()
    )


def _cache_path() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
    return Path(cache) / "gamehail" / "voices.json"


def catalogue(refresh: bool = False, timeout: float = 15.0) -> dict:
    """piper's published voice index, cached locally. Empty dict if unavailable."""
    cache = _cache_path()
    if not refresh and cache.is_file():
        try:
            return json.loads(cache.read_text())
        except json.JSONDecodeError:
            pass
    try:
        with urllib.request.urlopen(CATALOGUE_URL, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        log.warning("could not fetch the voice catalogue: %s", exc)
        return {}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return data


def available(language_prefix: str = "en") -> list[str]:
    """Catalogue keys for one language, best quality last, e.g. `en_US-amy-medium`."""
    keys = [k for k in catalogue() if k.startswith(f"{language_prefix}_")]
    order = {"x_low": 0, "low": 1, "medium": 2, "high": 3}
    return sorted(keys, key=lambda k: (k.rsplit("-", 1)[0], order.get(k.rsplit("-", 1)[1], 9)))


def download(key: str, progress=None, timeout: float = 60.0) -> Path:
    """Fetch one voice into the voices directory and return its .onnx path.

    `progress` is called with (fraction, label) if given.
    """
    entry = catalogue().get(key)
    if not entry:
        raise ValueError(f"unknown voice {key!r}")
    dest_dir = voices_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    wanted = [p for p in entry["files"] if p.endswith((".onnx", ".onnx.json"))]
    onnx_path = dest_dir / f"{key}.onnx"
    for index, remote in enumerate(wanted):
        suffix = ".onnx.json" if remote.endswith(".onnx.json") else ".onnx"
        target = dest_dir / f"{key}{suffix}"
        if target.is_file() and target.stat().st_size > 0:
            continue
        if progress:
            progress(index / len(wanted), f"downloading {key}{suffix}")
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(f"{BASE}/{remote}?download=true",
                                        timeout=timeout) as response, \
                    tmp.open("wb") as out:
                while chunk := response.read(1 << 16):
                    out.write(chunk)
        except (urllib.error.URLError, OSError, TimeoutError):
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(target)
    if progress:
        progress(1.0, f"{key} ready")
    return onnx_path


def label(path: Path | None) -> str:
    return path.stem if path else "default"
