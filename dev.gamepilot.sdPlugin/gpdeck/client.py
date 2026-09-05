"""Talk to the gamepilot daemon over its control socket.

Deliberately tiny and standard-library only: the plugin runs under OpenDeck's own
interpreter environment, so it cannot rely on anything installed in gamepilot's venv.
"""

import json
import os
import socket


def socket_path():
    override = os.environ.get("GAMEPILOT_SOCKET")
    if override:
        return override
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, "gamepilot.sock")


class NotRunning(Exception):
    """The daemon is not listening. Keys should say so rather than fail silently."""


def send(msg, timeout=20.0):
    path = socket_path()
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(path)
    except (OSError, socket.timeout) as exc:
        raise NotRunning(f"{path}: {exc}") from exc
    try:
        sock.sendall((json.dumps(msg) + "\n").encode())
        stream = sock.makefile("rb")
        line = stream.readline()
    finally:
        sock.close()
    if not line:
        raise NotRunning("daemon closed the connection")
    return json.loads(line.decode())


def press(action):
    return send({"cmd": "press", "action": action})


def release(action):
    return send({"cmd": "release", "action": action})


def ask(text, route=None, channels=None):
    msg = {"cmd": "ask", "text": text}
    if route:
        msg["route"] = route
    if channels:
        msg["channels"] = channels
    return send(msg)


def cancel():
    return send({"cmd": "cancel"})


def mute(on):
    return send({"cmd": "mute", "on": bool(on)})


def status(timeout=3.0):
    return send({"cmd": "status"}, timeout=timeout)
