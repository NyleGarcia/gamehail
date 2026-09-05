"""Streaming text-to-speech through piper, fanned out to one or more audio channels.

A channel is a PipeWire sink plus the `application.name` the stream carries. That is
what makes the two-destination setup work: the private channel goes to what you are
listening to, the broadcast channel goes to a sink your voice app captures (OpenWave's
Chat Mix, or any null sink), and OpenWave can bind each `application.name` to its own
matrix row.

Sentences are spoken as soon as they are complete, so speech starts while Claude is
still writing the rest of the answer.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from queue import Empty, Queue

from .config import TtsChannel, TtsConfig

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+|\n+")
_MARKUP = re.compile(r"[*_`#>]|\[[^\]]*\]\([^)]*\)")


def strip_markup(text: str) -> str:
    return _MARKUP.sub("", text).strip()


def piper_binary() -> str | None:
    """Locate piper without trusting PATH.

    piper is installed inside the venv, and systemd builds a PATH for user units that
    does not include it - so `shutil.which` finds it in a shell and not in the service,
    which makes the daemon silently mute. Look next to the running interpreter too.
    """
    found = shutil.which("piper")
    if found:
        return found
    beside = Path(sys.executable).parent / "piper"
    return str(beside) if beside.is_file() else None


def voice_samplerate(model: Path | None) -> int:
    """Piper voices ship their sample rate in the sidecar json (16k or 22.05k)."""
    sidecar = model.with_suffix(model.suffix + ".json") if model else None
    if sidecar and sidecar.is_file():
        try:
            return int(json.loads(sidecar.read_text())["audio"]["sample_rate"])
        except (KeyError, ValueError, OSError) as exc:
            log.warning("could not read sample rate from %s: %s", sidecar, exc)
    return 22050


class Speaker:
    """Serialises synthesis on a worker thread so sentences never overlap."""

    def __init__(self, cfg: TtsConfig):
        self.cfg = cfg
        self._queue: Queue[tuple[str, tuple[str, ...]] | None] = Queue()
        self._thread: threading.Thread | None = None
        self._procs: list[subprocess.Popen] = []
        self._stop = threading.Event()
        self._buffer = ""
        self._route: tuple[str, ...] = ()

    # -- channels ----------------------------------------------------------
    def channel(self, name: str) -> TtsChannel | None:
        for ch in self.cfg.channels:
            if ch.name == name:
                return ch
        log.warning("unknown tts channel %r; known: %s",
                    name, [c.name for c in self.cfg.channels])
        return None

    def resolve(self, names: tuple[str, ...] | list[str]) -> list[TtsChannel]:
        chans = [self.channel(n) for n in names]
        return [c for c in chans if c and c.enabled]

    def voice_for(self, ch: TtsChannel) -> Path | None:
        return ch.voice_model or self.cfg.voice_model

    @property
    def unavailable_reason(self) -> str | None:
        """Why speech is off, in words a settings window can show."""
        if not self.cfg.enabled:
            return "speech is switched off"
        if not piper_binary():
            return "piper not found — run `uv sync`"
        if not any(ch.enabled for ch in self.cfg.channels):
            return "no output channel is enabled"
        for ch in self.cfg.channels:
            if ch.enabled and (voice := self.voice_for(ch)) and Path(voice).exists():
                return None
        return "no voice model — pick one under Get more voices…"

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    # -- worker ------------------------------------------------------------
    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except Empty:
                continue
            if item is None:
                return
            text, route = item
            self._say(text, route)

    # -- synthesis + playback ----------------------------------------------
    def _synth(self, text: str, voice: Path) -> bytes:
        piper = piper_binary()
        if not piper:
            log.error("piper not found; cannot speak")
            return b""
        cmd = [piper, "--model", str(voice), "--output-raw"]
        if self.cfg.speaker is not None:
            cmd += ["--speaker", str(self.cfg.speaker)]
        if self.cfg.length_scale is not None:
            cmd += ["--length-scale", str(self.cfg.length_scale)]
        done = subprocess.run(
            cmd, input=text.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return done.stdout

    def _player_cmd(self, ch: TtsChannel, rate: int) -> list[str]:
        if self.cfg.player == "aplay":
            # No per-stream routing; whatever the default sink is.
            return ["aplay", "-q", "-r", str(rate), "-f", "S16_LE", "-t", "raw", "-c", "1"]
        cmd = [
            "pw-play", "--raw",
            "--format", "s16", "--rate", str(rate), "--channels", "1",
            "--volume", str(ch.volume),
            # No --media-role: WirePlumber's role policy ducks a Notification stream,
            # which measured ~25 dB down at the headphones - inaudible under game audio.
            "-P", f'{{ application.name = "{ch.app_name}" '
                  f'media.name = "gamepilot {ch.name}" }}',
        ]
        if ch.target and ch.target != "default":
            cmd += ["--target", ch.target]
        return cmd + ["-"]

    @staticmethod
    def _feed(proc: subprocess.Popen, pcm: bytes) -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(pcm)
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            log.warning("player write failed: %s", exc)

    def _say(self, text: str, route: tuple[str, ...]) -> None:
        channels = self.resolve(route)
        if not channels:
            return
        # One synthesis per distinct voice, then fan the PCM out to every channel
        # using it so the destinations stay in sync.
        by_voice: dict[Path, list[TtsChannel]] = {}
        for ch in channels:
            voice = self.voice_for(ch)
            voice = Path(voice) if voice else None
            if not voice or not voice.exists():
                log.warning("channel %s has no usable voice model", ch.name)
                continue
            by_voice.setdefault(voice, []).append(ch)

        for voice, chans in by_voice.items():
            try:
                pcm = self._synth(text, voice)
            except (OSError, subprocess.SubprocessError) as exc:
                log.error("piper failed: %s", exc)
                continue
            if not pcm:
                continue
            rate = voice_samplerate(voice)
            players: list[subprocess.Popen] = []
            for ch in chans:
                try:
                    proc = subprocess.Popen(
                        self._player_cmd(ch, rate),
                        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    )
                except OSError as exc:
                    log.error("could not start player for channel %s: %s", ch.name, exc)
                    continue
                players.append(proc)
            self._procs = players
            # One writer thread per player. pw-play consumes at playback rate, so
            # writing the channels one after another leaves the second player with an
            # idle stdin for the whole of the first playback - long enough that it
            # exits, and the write that finally arrives dies with a broken pipe.
            writers = [
                threading.Thread(target=self._feed, args=(proc, pcm),
                                 name="tts-write", daemon=True)
                for proc in players
            ]
            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join(timeout=180)
            for proc in players:
                try:
                    proc.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    log.warning("player did not finish; killing it")
                    proc.kill()
            self._procs = []

    # -- streaming API -----------------------------------------------------
    def begin(self, route: tuple[str, ...] | list[str]) -> None:
        """Set the destination channels for the answer that is about to stream."""
        self._buffer = ""
        self._route = tuple(route)

    def feed(self, chunk: str) -> None:
        """Accumulate streamed text and enqueue each finished sentence."""
        if not self.available or not self._route:
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
                self._queue.put((sentence, self._route))

    def flush(self) -> None:
        """Speak whatever is left in the buffer."""
        tail, self._buffer = strip_markup(self._buffer), ""
        if tail and self.available and self._route:
            self._ensure_worker()
            self._queue.put((tail, self._route))

    def cancel(self) -> None:
        """Drop queued speech and kill anything mid-sentence."""
        self._buffer = ""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        for proc in self._procs:
            if proc.poll() is None:
                proc.kill()

    def close(self) -> None:
        self.cancel()
        self._stop.set()
        self._queue.put(None)
