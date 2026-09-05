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
from . import gamemodules
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
        self.module: gamemodules.GameModule | None = None
        self._module_checked = 0.0
        self.refresh_module(force=True)

    # -- ui helpers --------------------------------------------------------
    def _emit(self, kind: str, payload: str = "") -> None:
        if kind == "status":
            self.state = payload
        elif kind == "answer" and payload:
            self.state = "idle"
        elif kind == "hide":
            self.state = "idle"
        self.events.put((kind, payload))

    # -- game modules ------------------------------------------------------
    def refresh_module(self, force: bool = False) -> gamemodules.GameModule | None:
        """Pick the module for whatever is running, and reconfigure if it changed.

        Detection reads /proc, so it is throttled: a question every few seconds should
        not re-scan the process table each time.
        """
        now = time.monotonic()
        if not force and now - self._module_checked < 5.0:
            return self.module
        self._module_checked = now
        cfg = self.cfg.games
        module = gamemodules.resolve(cfg.default, cfg.auto_switch, cfg.override or None)
        if module is None or (self.module and module.id == self.module.id):
            return self.module
        self.apply_module(module)
        return self.module

    def apply_module(self, module: gamemodules.GameModule) -> None:
        """Point the backend and the recogniser at one game."""
        previous = self.module.id if self.module else None
        self.module = module
        backend = self.cfg.backend
        if module.system_prompt:
            backend.system_prompt = module.system_prompt.strip()
        if module.allowed_tools:
            backend.allowed_tools = list(module.allowed_tools)
        if module.model:
            backend.model = module.model
        if module.effort:
            backend.effort = module.effort
        backend.mcp_config = module.mcp_config_path(self.cfg.work_dir)
        self.cfg.stt.vocabulary = list(module.vocabulary)
        self.transcriber.cfg = self.cfg.stt
        if previous is not None:
            # The warm session carries the old game's prompt and tools; start a new one.
            log.info("game module %s -> %s, restarting the session", previous, module.id)
            self.reset_backend()
        else:
            log.info("game module: %s (%s)", module.name, module.id)

    def status(self) -> dict:
        """Everything a control surface needs to label its keys."""
        return {
            "state": self.state,
            "game": self.module.id if self.module else "",
            "game_name": self.module.name if self.module else "",
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
            self.refresh_module()
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
        self.refresh_module()
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
    def reset_backend(self) -> None:
        """Drop the warm session and re-warm it in the background.

        Every reset - a game switch, the tray's "New context", `gamehail ctl reset` -
        should go through here rather than calling backend.reset() directly, so the
        next real question does not pay the cold-start ToolSearch cost that a bare
        reset would otherwise hand it.
        """
        self.backend.reset()
        threading.Thread(target=self.warmup_backend, name="backend-rewarm",
                         daemon=True).start()

    def warmup_backend(self) -> None:
        """Pay the first-turn cost once at startup instead of on the pilot's first real
        question. Claude Code defers loading MCP tool schemas until searched for -
        measured here at several extra seconds on a session's first tool call, gone by
        the second. The warmup question must actually call a tool (a trivial one) to pay
        that cost; a tool-free reply would warm nothing. Runs the backend directly,
        bypassing ask()'s UI events - nothing should flash on the overlay or be spoken.
        """
        try:
            for chunk in self.backend.ask(
                "Use a scmcp tool to fetch the current sell price for Laranite, then "
                "reply with just the number. This is a warmup call, not a real question."
            ):
                if chunk.final:
                    break
        except Exception as exc:  # noqa: BLE001 - a slow first real question, not fatal
            log.warning("backend warmup failed: %s", exc)

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
