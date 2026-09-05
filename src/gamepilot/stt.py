"""Speech-to-text via faster-whisper, GPU when it works, CPU otherwise."""

from __future__ import annotations

import logging

import numpy as np

from .config import SttConfig

log = logging.getLogger(__name__)


class Transcriber:
    """Lazily loads a Whisper model; falls back to CPU int8 if CUDA init fails."""

    def __init__(self, cfg: SttConfig):
        self.cfg = cfg
        self._model = None

    def _load(self):
        from faster_whisper import WhisperModel

        wanted = self.cfg.device
        attempts: list[tuple[str, str]] = []
        if wanted in ("auto", "cuda"):
            attempts.append(("cuda", "float16" if self.cfg.compute_type == "auto" else self.cfg.compute_type))
        attempts.append(("cpu", "int8" if self.cfg.compute_type == "auto" else self.cfg.compute_type))

        last: Exception | None = None
        for device, compute in attempts:
            try:
                model = WhisperModel(self.cfg.model, device=device, compute_type=compute)
                log.info("whisper %s loaded on %s/%s", self.cfg.model, device, compute)
                return model
            except Exception as exc:  # noqa: BLE001 - ctranslate2 raises many types
                log.warning("whisper load failed on %s/%s: %s", device, compute, exc)
                last = exc
        raise RuntimeError(f"could not load whisper model {self.cfg.model!r}") from last

    def warmup(self) -> None:
        if self._model is None:
            self._model = self._load()

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < self.cfg.samplerate * 0.25:  # under 250ms is a mis-tap
            return ""
        self.warmup()
        segments, _info = self._model.transcribe(  # type: ignore[union-attr]
            audio,
            language=self.cfg.language,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
