"""Push-to-talk microphone capture."""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)


class Recorder:
    """Records mono float32 audio between :meth:`start` and :meth:`stop`.

    Uses sounddevice (PortAudio -> PipeWire). Frames are appended from the audio
    callback thread, so the buffer list is guarded by a lock.
    """

    def __init__(self, samplerate: int = 16000, max_seconds: float = 30.0,
                 device: str | int | None = None):
        self.samplerate = samplerate
        self.device = device or None
        self.max_frames = int(samplerate * max_seconds)
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        if self._stream is not None:
            return
        with self._lock:
            self._frames = []

        def cb(indata, _frames, _time, status):  # pragma: no cover - realtime callback
            if status:
                log.debug("audio status: %s", status)
            with self._lock:
                self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self.samplerate, channels=1, dtype="float32", callback=cb,
            device=self.device,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured mono waveform (may be empty)."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            frames = self._frames
            self._frames = []
        if not frames:
            return np.zeros(0, dtype="float32")
        audio = np.concatenate(frames, axis=0).reshape(-1)
        return audio[: self.max_frames]

    @property
    def active(self) -> bool:
        return self._stream is not None


def input_devices() -> list[str]:
    """Names of every capture-capable device PortAudio can see."""
    try:
        import sounddevice as sd

        return [
            d["name"] for d in sd.query_devices()
            if d.get("max_input_channels", 0) > 0
        ]
    except Exception as exc:  # noqa: BLE001 - audio stack may be absent
        log.warning("could not enumerate input devices: %s", exc)
        return []
