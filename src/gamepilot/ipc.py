"""Control socket: newline-delimited JSON over a Unix socket.

This is how anything that is not a keyboard drives gamepilot - the Stream Deck /
OpenDeck plugin, `gamepilot ctl`, a shell script, a macro on the HOTAS. A Stream Deck
key sends keyDown and keyUp as separate events, which maps exactly onto press/release,
so hold-to-talk works from a deck key the same way it works from a held key.

Protocol: one JSON object per line in, one JSON object per line out.

    {"cmd": "press",   "action": "ask_voice"}      -> {"ok": true, ...}
    {"cmd": "release", "action": "ask_voice"}
    {"cmd": "ask",     "text": "...", "channels": ["me"]}
    {"cmd": "cancel"} | {"cmd": "reset"} | {"cmd": "status"}
    {"cmd": "mute",    "on": true}
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ACTIONS = ("ask_voice", "ask_screen", "ask_broadcast", "cancel")


def default_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "gamepilot.sock"


class ControlServer:
    """Serves control commands against a live :class:`~gamepilot.pipeline.Pipeline`."""

    def __init__(self, pipeline, path: Path | None = None):
        self.pipeline = pipeline
        self.path = path or default_socket_path()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- command handling --------------------------------------------------
    def handle(self, msg: dict[str, Any]) -> dict[str, Any]:
        cmd = msg.get("cmd")
        pipe = self.pipeline
        try:
            if cmd in ("press", "release"):
                action = msg.get("action", "ask_voice")
                if action not in ACTIONS:
                    return {"ok": False, "error": f"unknown action {action!r}"}
                pipe.trigger(action, cmd == "press")
                return {"ok": True, "action": action, "pressed": cmd == "press"}

            if cmd == "ask":
                text = (msg.get("text") or "").strip()
                if not text:
                    return {"ok": False, "error": "empty text"}
                channels = msg.get("channels") or pipe.route_for(
                    msg.get("route", "ask_voice")
                )
                # Answer on a worker so the caller's key is not held open for it.
                threading.Thread(
                    target=pipe.ask, args=(text, None, channels),
                    name="ipc-ask", daemon=True,
                ).start()
                return {"ok": True, "text": text, "channels": list(channels)}

            if cmd == "cancel":
                pipe.trigger("cancel", True)
                return {"ok": True}

            if cmd == "reset":
                pipe.backend.reset()
                return {"ok": True}

            if cmd == "mute":
                on = bool(msg.get("on", True))
                pipe.speaker.cfg.enabled = not on
                if on:
                    pipe.speaker.cancel()
                return {"ok": True, "muted": on}

            if cmd == "status":
                return {"ok": True, **pipe.status()}

            return {"ok": False, "error": f"unknown cmd {cmd!r}"}
        except Exception as exc:  # noqa: BLE001 - a bad command must not kill the daemon
            log.exception("control command failed: %s", msg)
            return {"ok": False, "error": str(exc)}

    # -- server ------------------------------------------------------------
    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(
                target=self._client, args=(conn,), name="ipc-client", daemon=True
            ).start()

    def _client(self, conn: socket.socket) -> None:
        with conn, conn.makefile("rwb") as stream:
            for raw in stream:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    reply = {"ok": False, "error": f"bad json: {exc}"}
                else:
                    reply = self.handle(msg)
                try:
                    stream.write((json.dumps(reply) + "\n").encode())
                    stream.flush()
                except (BrokenPipeError, OSError):
                    return

    def start(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            # A leftover socket from a killed daemon would make bind() fail.
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.5)
                probe.connect(str(self.path))
                probe.close()
                raise RuntimeError(f"another gamepilot is listening on {self.path}")
            except (ConnectionRefusedError, FileNotFoundError, OSError):
                self.path.unlink(missing_ok=True)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.path))
        self.path.chmod(0o600)
        self._sock.listen(8)
        self._thread = threading.Thread(target=self._serve, name="ipc", daemon=True)
        self._thread.start()
        log.info("control socket at %s", self.path)
        return self.path

    def close(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
            self._sock = None
        self.path.unlink(missing_ok=True)


def send(msg: dict[str, Any], path: Path | None = None, timeout: float = 30.0) -> dict:
    """Send one command to a running daemon and return its reply."""
    path = path or default_socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall((json.dumps(msg) + "\n").encode())
        with sock.makefile("rb") as stream:
            line = stream.readline()
    if not line:
        return {"ok": False, "error": "no reply"}
    return json.loads(line.decode())
