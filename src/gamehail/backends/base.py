"""Backend protocol shared by the headless and persistent Claude Code drivers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable


@dataclass
class Chunk:
    """One piece of a streamed answer."""

    text: str
    final: bool = False


@runtime_checkable
class Backend(Protocol):
    def ask(self, prompt: str, images: list[Path] | None = None) -> Iterator[Chunk]:
        """Yield answer chunks as they arrive. Last chunk has final=True."""
        ...

    def reset(self) -> None:
        """Drop conversation state (new context)."""
        ...

    def close(self) -> None:
        ...
