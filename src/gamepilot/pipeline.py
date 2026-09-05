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
from .ipc import ControlServer
from .stt import Transcriber
from .tts import Speaker

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, cfg: Config, events: Queue | None = None):
        self.cfg = cfg
        self.events = events or Queue()
        self.backend = make_backend(cfg.backend)
        self.recorder = Recorder(cfg.stt.samplerate, cfg.stt.max_seconds, cfg.stt.input_device)
        self.transcriber = Transcriber(cfg.stt)
        self.speaker = Speaker(cfg.tts)
        self.shots = Screenshotter(cfg.screen, cfg.work_dir)
        self.hotkeys = HotkeyListener(cfg.hotkeys, self.trigger)
        self.control = ControlServer(self)

        self._busy = threading.Lock()
        self._press_at: dict[str, float] = {}
        self._pending_shot: Path | None = None
        self.state = "idle"
        self.last_question = ""
        self.last_answer = ""

    # -- ui helpers --------------------------------------------------------
    def _emit(self, kind: str, payload: str = "") -> None:
        if kind == "status":
            self.state = payload
        elif kind == "answer" and payload:
            self.state = "idle"
        elif kind == "hide":
            self.state = "idle"
        self.events.put((kind, payload))

    def status(self) -> dict:
        """Everything a control surface needs to label its keys."""
        return {
            "state": self.state,
            "busy": self._busy.locked(),
            "recording": self.recorder.active,
            "muted": not self.cfg.tts.enabled,
            "profile": self.cfg.profile,
            "backend": self.cfg.backend.mode,
            "model": self.cfg.backend.model,
            "question": self.last_question,
            "answer": self.last_answer,
            "channels": [c.name for c in self.cfg.tts.channels if c.enabled],
            "routes": self.cfg.tts.routes,
        }

    # -- triggers (hotkeys, control socket, deck keys) ----------------------
    def trigger(self, action: str, pressed: bool) -> None:
        """Start or finish a request. `pressed` is key-down; releasing runs the query.

        Both the evdev listener and the control socket land here, so a Stream Deck key
        behaves exactly like a held hotkey.
        """
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
            elif action == "ask_broadcast":
                self._emit("status", "listening (answer goes to the squad)")
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
            target=self._handle, args=(audio, shot, action), name="query", daemon=True
        ).start()

    # -- query --------------------------------------------------------------
    def route_for(self, action: str) -> list[str]:
        """Which audio channels an answer to this hotkey is spoken on."""
        return self.cfg.tts.routes.get(action, ["me"])

    def _handle(self, audio, shot: Path | None, action: str = "ask_voice") -> None:
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
                # Distinguish "said nothing" from "recording the wrong device": a
                # silent peak means the microphone never carried the voice at all.
                peak = float(abs(audio).max()) if audio.size else 0.0
                if peak < 0.01:
                    log.warning("mic peak %.3f - check the input device in settings", peak)
                    self._emit("answer",
                               f"heard nothing (mic level {peak:.2f} — wrong input "
                               "device?)")
                else:
                    self._emit("answer", "heard nothing")
                return

            log.info("Q: %s", question)
            self.last_question = question
            route = self.route_for(action)
            self._emit("status", f"» {question}")
            self.ask(question, [shot] if shot else None, channels=route)
        finally:
            self._busy.release()
            self.shots.prune()

    def ask(
        self,
        question: str,
        images: list[Path] | None = None,
        channels: list[str] | None = None,
    ) -> str:
        """Run one question through the backend, streaming to speech and overlay."""
        answer: list[str] = []
        route = channels or self.route_for("ask_voice")
        self.speaker.begin(route)
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
        self.last_answer = text
        log.info("A (%.1fs, -> %s): %s", time.monotonic() - started, ",".join(route), text)
        # The streamed text arrived as `append` events, which say nothing about being
        # finished. Without this closing event the state stays on the question: the
        # deck key sits on "thinking…", and the overlay never starts its hide timer.
        self._emit("answer", text or "no answer")
        return text

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self.cfg.hotkeys.enabled:
            self.hotkeys.start()
        else:
            log.debug("keyboard/HOTAS hotkeys disabled; triggering via the control socket")
        # The socket is the primary trigger now, so a daemon that cannot bind it has
        # nothing to listen to - fail loudly instead of sitting there looking alive.
        self.control.start()

    def close(self) -> None:
        for step, shutdown in (
            ("control socket", self.control.close),
            ("hotkeys", self.hotkeys.stop if self.cfg.hotkeys.enabled else lambda: None),
            ("speech", self.speaker.close),
            ("claude session", self.backend.close),
        ):
            started = time.monotonic()
            try:
                shutdown()
            except Exception as exc:  # noqa: BLE001 - one bad step must not strand the rest
                log.warning("closing %s failed: %s", step, exc)
            log.debug("closed %s in %.2fs", step, time.monotonic() - started)
