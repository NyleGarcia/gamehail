"""Streaming text-to-speech through the piper CLI.

Sentences are spoken as soon as they are complete, so speech starts while Claude is
still writing the rest of the answer.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from queue import Empty, Queue

from .config import TtsConfig

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+|\n+")
_MARKUP = re.compile(r"[*_`#>]|\[[^\]]*\]\([^)]*\)")


def strip_markup(text: str) -> str:
    return _MARKUP.sub("", text).strip()


class Speaker:
    """Serialises synthesis on a worker thread so sentences never overlap."""

    def __init__(self, cfg: TtsConfig):
        self.cfg = cfg
        self._queue: Queue[str | None] = Queue()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._buffer = ""

    def _samplerate(self) -> int:
        """Piper voices ship their sample rate in the sidecar json (16k or 22.05k)."""
        model = self.cfg.voice_model
        sidecar = model.with_suffix(model.suffix + ".json") if model else None
        if sidecar and sidecar.is_file():
            try:
                return int(json.loads(sidecar.read_text())["audio"]["sample_rate"])
            except (KeyError, ValueError, OSError) as exc:
                log.warning("could not read sample rate from %s: %s", sidecar, exc)
        return 22050

    def _player_cmd(self) -> list[str]:
        cmd = list(self.cfg.player)
        if "-r" in cmd:
            cmd[cmd.index("-r") + 1] = str(self._samplerate())
        return cmd

    @property
    def available(self) -> bool:
        return bool(
            self.cfg.enabled
            and self.cfg.voice_model
            and self.cfg.voice_model.exists()
            and shutil.which("piper")
        )

    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=0.25)
            except Empty:
                continue
            if text is None:
                return
            self._synth(text)

    def _synth(self, text: str) -> None:
        piper = ["piper", "--model", str(self.cfg.voice_model), "--output-raw"]
        if self.cfg.speaker is not None:
            piper += ["--speaker", str(self.cfg.speaker)]
        if self.cfg.length_scale is not None:
            piper += ["--length-scale", str(self.cfg.length_scale)]
        try:
            self._proc = subprocess.Popen(
                piper, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            player = subprocess.Popen(
                self._player_cmd(), stdin=self._proc.stdout, stderr=subprocess.DEVNULL
            )
            assert self._proc.stdin is not None
            self._proc.stdin.write(text.encode())
            self._proc.stdin.close()
            player.wait()
            self._proc.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001 - never let TTS kill the pipeline
            log.error("tts failed: %s", exc)
        finally:
            self._proc = None

    # -- streaming API -----------------------------------------------------
    def feed(self, chunk: str) -> None:
        """Accumulate streamed text and enqueue each finished sentence."""
        if not self.available:
            return
        self._ensure_worker()
        self._buffer += chunk
        while True:
            match = _SENTENCE_END.search(self._buffer)
            if not match:
                break
            sentence, self._buffer = self._buffer[: match.end()], self._buffer[match.end():]
            sentence = strip_markup(sentence)
            if sentence:
                self._queue.put(sentence)

    def flush(self) -> None:
        """Speak whatever is left in the buffer."""
        if not self.available:
            self._buffer = ""
            return
        tail, self._buffer = strip_markup(self._buffer), ""
        if tail:
            self._ensure_worker()
            self._queue.put(tail)

    def cancel(self) -> None:
        """Drop queued speech and kill anything mid-sentence."""
        self._buffer = ""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        proc = self._proc
        if proc and proc.poll() is None:
            proc.kill()

    def close(self) -> None:
        self.cancel()
        self._stop.set()
        self._queue.put(None)
