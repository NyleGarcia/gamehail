"""Drive the `claude` CLI in headless mode.

Two modes, same argv builder:

* :class:`OneshotBackend` spawns `claude -p ... --output-format stream-json` per query.
  No context carried between questions; ~3s to first answer.
* :class:`PersistentBackend` keeps one `claude -p --input-format stream-json` process
  alive and writes each question to its stdin. Follow-up turns land in ~1s because the
  prompt prefix (system prompt + MCP tool schemas) stays cached server-side.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Iterator

from ..config import BackendConfig
from .base import Chunk

log = logging.getLogger(__name__)


def _claude_bin() -> str:
    exe = os.environ.get("GAMEHAIL_CLAUDE_BIN") or shutil.which("claude")
    if not exe:
        raise RuntimeError("`claude` CLI not found on PATH (set GAMEHAIL_CLAUDE_BIN)")
    return exe


def build_argv(cfg: BackendConfig, *, streaming_stdin: bool) -> list[str]:
    argv = [
        _claude_bin(),
        "-p",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", cfg.model,
        "--system-prompt", cfg.system_prompt,
        "--permission-mode", cfg.permission_mode,
        "--permission-prompts", "none",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if streaming_stdin:
        argv += ["--input-format", "stream-json"]
    if cfg.restricted:
        argv.append("--restricted")
    if cfg.effort:
        argv += ["--effort", cfg.effort]
    if cfg.mcp_config:
        argv += ["--mcp-config", str(cfg.mcp_config)]
    if cfg.allowed_tools:
        argv += ["--allowed-tools", *cfg.allowed_tools]
    for d in cfg.add_dirs:
        argv += ["--add-dir", str(d)]
    return argv


def _compose(prompt: str, images: list[Path] | None) -> str:
    if not images:
        return prompt
    paths = "\n".join(f"- {p}" for p in images)
    return (
        f"{prompt}\n\nScreenshots from the running game (read them with the Read tool):\n{paths}"
    )


def _events_to_chunks(lines: Iterator[str]) -> Iterator[Chunk]:
    """Translate a claude stream-json event stream into text chunks.

    Prefers `content_block_delta` partials so speech can start before the answer is
    complete, and falls back to the terminal `result` payload when no partials arrived.
    """
    streamed = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            log.debug("non-json line from claude: %.120s", line)
            continue

        kind = ev.get("type")
        if kind == "stream_event":
            inner = ev.get("event", {})
            if inner.get("type") == "content_block_delta":
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    streamed = True
                    yield Chunk(delta["text"])
        elif kind == "result":
            text = ev.get("result") or ""
            if ev.get("is_error"):
                yield Chunk(f"[backend error] {text}".strip(), final=True)
            elif streamed:
                yield Chunk("", final=True)
            else:
                yield Chunk(text, final=True)
            return
    yield Chunk("", final=True)


class OneshotBackend:
    """One `claude` process per question. Stateless, simplest, slowest to first token."""

    def __init__(self, cfg: BackendConfig):
        self.cfg = cfg
        self._proc: subprocess.Popen[str] | None = None

    def ask(self, prompt: str, images: list[Path] | None = None) -> Iterator[Chunk]:
        argv = build_argv(self.cfg, streaming_stdin=False)
        argv += ["--session-id", str(uuid.uuid4()), _compose(prompt, images)]
        log.debug("oneshot argv: %s", argv)
        self._proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1
        )
        try:
            assert self._proc.stdout is not None
            yield from _events_to_chunks(iter(self._proc.stdout.readline, ""))
        finally:
            self.close()

    def reset(self) -> None:  # stateless already
        pass

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


class PersistentBackend:
    """One long-lived `claude` process fed over stdin. Warm cache, ~1s follow-ups."""

    def __init__(self, cfg: BackendConfig):
        self.cfg = cfg
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._turns = 0

    # -- process lifecycle -------------------------------------------------
    def _ensure(self) -> subprocess.Popen[str]:
        if self._proc and self._proc.poll() is None:
            return self._proc
        argv = build_argv(self.cfg, streaming_stdin=True)
        log.debug("persistent argv: %s", argv)
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._lines = queue.Queue()
        self._turns = 0
        self._reader = threading.Thread(
            target=self._pump, args=(self._proc,), name="claude-stdout", daemon=True
        )
        self._reader.start()
        return self._proc

    def _pump(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # process exited

    def _drain(self) -> Iterator[str]:
        """Yield stdout lines until the current turn's `result` event is consumed."""
        while True:
            item = self._lines.get(timeout=self.cfg.timeout_s)
            if item is None:
                return
            yield item

    # -- Backend protocol --------------------------------------------------
    def ask(self, prompt: str, images: list[Path] | None = None) -> Iterator[Chunk]:
        with self._lock:
            if self.cfg.max_turns and self._turns >= self.cfg.max_turns:
                log.info("persistent session hit max_turns=%s, restarting", self.cfg.max_turns)
                self.reset()
            proc = self._ensure()
            assert proc.stdin is not None
            msg = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": _compose(prompt, images)}],
                },
            }
            try:
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                self.reset()
                proc = self._ensure()
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
            self._turns += 1
            try:
                yield from _events_to_chunks(self._drain())
            except queue.Empty:
                yield Chunk("[backend timeout]", final=True)

    def reset(self) -> None:
        self.close()

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def make_backend(cfg: BackendConfig):
    if cfg.mode == "oneshot":
        return OneshotBackend(cfg)
    if cfg.mode == "persistent":
        return PersistentBackend(cfg)
    raise ValueError(f"unknown backend mode: {cfg.mode!r} (want 'persistent' or 'oneshot')")
