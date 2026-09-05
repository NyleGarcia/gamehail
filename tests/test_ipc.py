"""Control-socket protocol tests against a stub pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gamehail.ipc import ControlServer, send

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev.gamehail.sdPlugin"))


class StubSpeaker:
    def __init__(self):
        self.cfg = type("cfg", (), {"enabled": True})()
        self.cancelled = 0

    def cancel(self):
        self.cancelled += 1


class StubBackend:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1


class StubPipeline:
    """Only the surface ControlServer touches."""

    def __init__(self):
        self.triggers: list[tuple[str, bool]] = []
        self.asked: list[tuple] = []
        self.speaker = StubSpeaker()
        self.backend = StubBackend()

    def trigger(self, action, pressed):
        self.triggers.append((action, pressed))

    def ask(self, text, images=None, channels=None):
        self.asked.append((text, images, channels))
        return "answer"

    def route_for(self, action):
        return {"ask_broadcast": ["me", "squad"]}.get(action, ["me"])

    def status(self):
        return {"state": "idle", "muted": not self.speaker.cfg.enabled}


@pytest.fixture
def server(tmp_path):
    pipe = StubPipeline()
    srv = ControlServer(pipe, tmp_path / "gp.sock")
    srv.start()
    yield srv, pipe
    srv.close()


def test_press_and_release_reach_the_pipeline(server):
    srv, pipe = server
    assert send({"cmd": "press", "action": "ask_voice"}, srv.path)["ok"]
    assert send({"cmd": "release", "action": "ask_voice"}, srv.path)["ok"]
    assert pipe.triggers == [("ask_voice", True), ("ask_voice", False)]


def test_unknown_action_is_refused(server):
    srv, pipe = server
    reply = send({"cmd": "press", "action": "launch_missiles"}, srv.path)
    assert reply["ok"] is False
    assert pipe.triggers == []


def test_ask_uses_the_route_when_no_channels_given(server):
    srv, pipe = server
    reply = send({"cmd": "ask", "text": "eta", "route": "ask_broadcast"}, srv.path)
    assert reply["channels"] == ["me", "squad"]
    # ask() runs on a worker thread; give it a moment to land.
    for _ in range(50):
        if pipe.asked:
            break
        import time

        time.sleep(0.02)
    assert pipe.asked[0][0] == "eta"


def test_empty_question_is_refused(server):
    srv, _ = server
    assert send({"cmd": "ask", "text": "   "}, srv.path)["ok"] is False


def test_mute_toggles_and_reports(server):
    srv, pipe = server
    assert send({"cmd": "mute", "on": True}, srv.path)["muted"] is True
    assert pipe.speaker.cfg.enabled is False
    assert pipe.speaker.cancelled == 1
    send({"cmd": "mute", "on": False}, srv.path)
    assert pipe.speaker.cfg.enabled is True


def test_reset_restarts_the_backend(server):
    srv, pipe = server
    assert send({"cmd": "reset"}, srv.path)["ok"]
    assert pipe.backend.resets == 1


def test_bad_json_does_not_kill_the_server(server):
    import socket

    srv, _ = server
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(srv.path))
        sock.sendall(b"not json\n")
        reply = sock.makefile("rb").readline()
    assert b"bad json" in reply
    assert send({"cmd": "status"}, srv.path)["ok"]


def test_unknown_command_is_reported(server):
    srv, _ = server
    assert send({"cmd": "explode"}, srv.path)["ok"] is False
