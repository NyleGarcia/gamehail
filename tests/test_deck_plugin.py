"""End-to-end: a deck key press must reach the daemon.

A mock OpenDeck (a minimal WebSocket server) launches the real plugin process, sends it
the events a deck sends when a key is placed and pressed, and the stub daemon on the
other side records what arrived. This is the test that would have caught the plugin
dying silently after registration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gamehail.ipc import ControlServer
from test_ipc import StubPipeline

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "dev.gamehail.sdPlugin"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # the RFC 6455 handshake constant


def _accept_handshake(conn: socket.socket) -> None:
    request = b""
    while b"\r\n\r\n" not in request:
        chunk = conn.recv(4096)
        if not chunk:
            raise ConnectionError("client left during handshake")
        request += chunk
    key = ""
    for line in request.decode("latin-1").splitlines():
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    conn.sendall(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
    )


def _send_text(conn: socket.socket, payload: dict) -> None:
    data = json.dumps(payload).encode()
    header = bytearray([0x81])  # FIN + text; server frames are not masked
    if len(data) < 126:
        header.append(len(data))
    else:
        header.append(126)
        header += struct.pack(">H", len(data))
    conn.sendall(bytes(header) + data)


def _recv_text(conn: socket.socket, timeout: float = 5.0) -> dict | None:
    conn.settimeout(timeout)
    try:
        first = conn.recv(2)
        if len(first) < 2:
            return None
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", conn.recv(2))[0]
        mask = conn.recv(4) if first[1] & 0x80 else None
        data = b""
        while len(data) < length:
            data += conn.recv(length - len(data))
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return json.loads(data.decode())
    except (socket.timeout, OSError, ValueError):
        return None


class MockOpenDeck:
    """Just enough of the host to drive a plugin: handshake, register, events."""

    def __init__(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.conn: socket.socket | None = None
        self.registered = threading.Event()
        self.received: list[dict] = []

    def accept(self) -> None:
        self.server.settimeout(15)
        self.conn, _ = self.server.accept()
        _accept_handshake(self.conn)
        first = _recv_text(self.conn, timeout=10)
        if first and first.get("event") == "registerPlugin":
            self.registered.set()

    def pump(self, seconds: float = 1.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            message = _recv_text(self.conn, timeout=0.3)
            if message:
                self.received.append(message)

    def close(self) -> None:
        for sock in (self.conn, self.server):
            if sock:
                sock.close()


@pytest.fixture
def daemon(tmp_path):
    pipe = StubPipeline()
    server = ControlServer(pipe, tmp_path / "gp.sock")
    server.start()
    yield pipe, server.path
    server.close()


@pytest.mark.skipif(not (PLUGIN_DIR / "plugin.py").exists(), reason="plugin not present")
def test_key_press_reaches_the_daemon(daemon):
    pipe, socket_path = daemon
    host = MockOpenDeck()
    accepting = threading.Thread(target=host.accept, daemon=True)
    accepting.start()

    env = {**os.environ, "GAMEHAIL_SOCKET": str(socket_path)}
    proc = subprocess.Popen(
        [sys.executable, str(PLUGIN_DIR / "plugin.py"),
         "-port", str(host.port), "-pluginUUID", "dev.gamehail",
         "-registerEvent", "registerPlugin", "-info", "{}"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert host.registered.wait(15), "plugin never registered"

        context = "ctx-1"
        settings = {"route": "ask_broadcast"}
        for event in ("willAppear", "keyDown", "keyUp"):
            _send_text(host.conn, {
                "event": event, "action": "dev.gamehail.ask", "context": context,
                "payload": {"settings": settings},
            })
            time.sleep(0.2)
        host.pump(1.5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(pipe.triggers) < 2:
            time.sleep(0.1)
        assert pipe.triggers == [("ask_broadcast", True), ("ask_broadcast", False)]

        # And it told the key what was happening rather than leaving it blank.
        titles = [m["payload"]["title"] for m in host.received if m.get("event") == "setTitle"]
        assert "listening…" in titles and "thinking…" in titles

        assert proc.poll() is None, "plugin exited while the host was still connected"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        host.close()


@pytest.mark.skipif(not (PLUGIN_DIR / "plugin.py").exists(), reason="plugin not present")
def test_preset_key_sends_its_text(daemon):
    pipe, socket_path = daemon
    host = MockOpenDeck()
    threading.Thread(target=host.accept, daemon=True).start()

    env = {**os.environ, "GAMEHAIL_SOCKET": str(socket_path)}
    proc = subprocess.Popen(
        [sys.executable, str(PLUGIN_DIR / "plugin.py"),
         "-port", str(host.port), "-pluginUUID", "dev.gamehail",
         "-registerEvent", "registerPlugin", "-info", "{}"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert host.registered.wait(15)
        _send_text(host.conn, {
            "event": "keyDown", "action": "dev.gamehail.preset", "context": "ctx-2",
            "payload": {"settings": {"text": "eta to microtech", "route": "ask_voice"}},
        })
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not pipe.asked:
            time.sleep(0.1)
        assert pipe.asked and pipe.asked[0][0] == "eta to microtech"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        host.close()
