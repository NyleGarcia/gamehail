"""Screenshot capture for KDE Plasma on Wayland, downscaled to keep token cost low."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from ..config import ScreenConfig

log = logging.getLogger(__name__)


class Screenshotter:
    def __init__(self, cfg: ScreenConfig, work_dir: Path):
        self.cfg = cfg
        self.work_dir = work_dir

    def capture(self) -> Path | None:
        """Grab the game window (or full screen) and return a downscaled JPEG path."""
        if not self.cfg.enabled:
            return None
        if not shutil.which("spectacle"):
            log.error("spectacle not installed; screenshot capture disabled")
            return None

        stamp = time.strftime("%Y%m%d-%H%M%S")
        raw = self.work_dir / f"shot-{stamp}.png"
        flag = "-a" if self.cfg.mode == "active" else "-f"
        cmd = ["spectacle", "-b", "-n", flag, "-o", str(raw)]
        try:
            subprocess.run(cmd, check=True, timeout=15, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.error("spectacle failed: %s", exc)
            return None
        if not raw.exists():
            log.error("spectacle produced no file at %s", raw)
            return None

        out = raw.with_suffix(".jpg")
        if shutil.which("ffmpeg"):
            scale = f"scale='min({self.cfg.max_width},iw)':-2"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                     "-vf", scale, "-q:v", str(self.cfg.quality), str(out)],
                    check=True, timeout=20, capture_output=True,
                )
                raw.unlink(missing_ok=True)
                return out
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                log.warning("ffmpeg downscale failed, sending full-size PNG: %s", exc)
        return raw

    def prune(self, keep: int = 10) -> None:
        shots = sorted(self.work_dir.glob("shot-*"), key=lambda p: p.stat().st_mtime)
        for old in shots[:-keep]:
            old.unlink(missing_ok=True)
