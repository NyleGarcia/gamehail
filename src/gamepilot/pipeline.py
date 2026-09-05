"""Wires hotkeys -> capture -> Claude -> speech + overlay."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from queue import Queue

from .backends import make_backend
from .capture.audio import Recorder
from .capture.screen import Screenshotter
from .config import Config
from .hotkeys import HotkeyListener
from .stt import Transcriber
from .tts import Speaker

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, cfg: Config, events: Queue | None = None):
        self.cfg = cfg
        self.events = events or Queue()
        self.backend = make_backend(cfg.backend)
        self.recorder = Recorder(cfg.stt.samplerate, cfg.stt.max_seconds)
        self.transcriber = Transcriber(cfg.stt)
        self.speaker = Speaker(cfg.tts)
        self.shots = Screenshotter(cfg.screen, cfg.work_dir)
        self.hotkeys = HotkeyListener(cfg.hotkeys, self._on_key)

        self._busy = threading.Lock()
        self._press_at: dict[str, float] = {}
        self._pending_shot: Path | None = None

    # -- ui helpers --------------------------------------------------------
    def _emit(self, kind: str, payload: str = "") -> None:
        self.events.put((kind, payload))

    # -- hotkey handling ---------------------------------------------------
    def _on_key(self, action: str, pressed: bool) -> None:
        now = time.monotonic()
        if action == "cancel":
            if pressed:
                self.speaker.cancel()
                self._emit("hide")
            return

        if pressed:
            self._press_at[action] = now
            if action == "ask_screen":
                # Grab the frame at press time - the moment the user asked about.
                self._pending_shot = self.shots.capture()
                self._emit("status", "listening (screenshot captured)")
            else:
                self._emit("status", "listening")
            self.speaker.cancel()
            self.recorder.start()
            return

        held_ms = (now - self._press_at.pop(action, now)) * 1000
        audio = self.recorder.stop()
        if held_ms < self.cfg.hotkeys.min_hold_ms:
            self._emit("hide")
            self._pending_shot = None
            return

        shot = self._pending_shot
        self._pending_shot = None
        threading.Thread(
            target=self._handle, args=(audio, shot), name="query", daemon=True
        ).start()

    # -- query --------------------------------------------------------------
    def _handle(self, audio, shot: Path | None) -> None:
        if not self._busy.acquire(blocking=False):
            log.info("dropping query: one already in flight")
            return
        try:
            self._emit("status", "transcribing")
            try:
                question = self.transcriber.transcribe(audio)
            except Exception as exc:  # noqa: BLE001
                log.error("transcription failed: %s", exc)
                self._emit("answer", f"transcription failed: {exc}")
                return
            if not question:
                self._emit("answer", "heard nothing")
                return

            log.info("Q: %s", question)
            self._emit("status", f"» {question}")
            self.ask(question, [shot] if shot else None)
        finally:
            self._busy.release()
            self.shots.prune()

    def ask(self, question: str, images: list[Path] | None = None) -> str:
        """Run one question through the backend, streaming to speech and overlay."""
        answer: list[str] = []
        self._emit("answer", "")
        started = time.monotonic()
        for chunk in self.backend.ask(question, images):
            if chunk.text:
                answer.append(chunk.text)
                self._emit("append", chunk.text)
                self.speaker.feed(chunk.text)
            if chunk.final:
                self.speaker.flush()
        text = "".join(answer).strip()
        log.info("A (%.1fs): %s", time.monotonic() - started, text)
        if not text:
            self._emit("answer", "no answer")
        return text

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.hotkeys.start()

    def close(self) -> None:
        self.hotkeys.stop()
        self.speaker.close()
        self.backend.close()
