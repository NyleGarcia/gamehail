"""Speech-to-text via faster-whisper, GPU when it works, CPU otherwise."""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

import numpy as np

from .config import SttConfig

log = logging.getLogger(__name__)

_CUDA_LIBS = ("libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9")


def preload_cuda_libraries() -> bool:
    """Make the pip-installed CUDA runtime visible to ctranslate2.

    Arch ships no system cuBLAS, and the wheels put theirs under
    `site-packages/nvidia/*/lib`, which the dynamic loader does not search. Setting
    LD_LIBRARY_PATH would have to happen before the process starts; dlopen-ing each
    library here has the same effect and needs no wrapper script - once loaded, later
    lookups by SONAME resolve to it.
    """
    roots = [Path(p) / "nvidia" for p in sys.path if p.endswith("site-packages")]
    candidates: list[Path] = []
    for root in roots:
        if root.is_dir():
            candidates.extend(root.glob("*/lib"))
    loaded = 0
    for name in _CUDA_LIBS:
        for directory in candidates:
            target = directory / name
            if target.is_file():
                try:
                    ctypes.CDLL(str(target), mode=ctypes.RTLD_GLOBAL)
                    loaded += 1
                except OSError as exc:
                    log.debug("could not preload %s: %s", target, exc)
                break
    log.debug("preloaded %d/%d CUDA libraries", loaded, len(_CUDA_LIBS))
    return loaded == len(_CUDA_LIBS)


class Transcriber:
    """Lazily loads a Whisper model; falls back to CPU int8 if CUDA init fails."""

    def __init__(self, cfg: SttConfig):
        self.cfg = cfg
        self._model = None
        self._device = "cpu"

    def _load(self):
        from faster_whisper import WhisperModel

        wanted = self.cfg.device
        if wanted in ("auto", "cuda"):
            preload_cuda_libraries()
        attempts: list[tuple[str, str]] = []
        if wanted in ("auto", "cuda"):
            attempts.append(("cuda", "float16" if self.cfg.compute_type == "auto" else self.cfg.compute_type))
        attempts.append(("cpu", "int8" if self.cfg.compute_type == "auto" else self.cfg.compute_type))

        last: Exception | None = None
        for device, compute in attempts:
            try:
                model = WhisperModel(self.cfg.model, device=device, compute_type=compute)
                log.info("whisper %s loaded on %s/%s", self.cfg.model, device, compute)
                self._device = device
                return model
            except Exception as exc:  # noqa: BLE001 - ctranslate2 raises many types
                log.warning("whisper load failed on %s/%s: %s", device, compute, exc)
                last = exc
        raise RuntimeError(f"could not load whisper model {self.cfg.model!r}") from last

    def warmup(self) -> None:
        if self._model is None:
            self._model = self._load()

    @property
    def initial_prompt(self) -> str | None:
        if not self.cfg.vocabulary:
            return None
        return ", ".join(self.cfg.vocabulary) + "."

    def _run(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(  # type: ignore[union-attr]
            audio,
            language=self.cfg.language,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < self.cfg.samplerate * 0.25:  # under 250ms is a mis-tap
            return ""
        self.warmup()
        try:
            return self._run(audio)
        except RuntimeError as exc:
            # ctranslate2 loads the model happily and only fails when it first needs
            # cuBLAS, so a broken GPU setup surfaces here rather than at load time.
            # One question should not be lost to it: rebuild on CPU and answer.
            if self._device != "cpu":
                log.error("GPU transcription failed (%s); falling back to CPU", exc)
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.cfg.model, device="cpu",
                                           compute_type="int8")
                self._device = "cpu"
                return self._run(audio)
            raise
