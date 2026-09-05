"""The deck plugin's client speaks the same protocol the daemon serves."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev.gamehail.sdPlugin"))

from ghdeck import client  # noqa: E402

from test_ipc import StubPipeline  # noqa: E402
from gamehail.ipc import ControlServer  # noqa: E402


@pytest.fixture
def running(tmp_path, monkeypatch):
    pipe = StubPipeline()
    srv = ControlServer(pipe, tmp_path / "gp.sock")
    srv.start()
    monkeypatch.setenv("GAMEHAIL_SOCKET", str(srv.path))
    yield pipe
    srv.close()


def test_hold_to_talk_round_trip(running):
    client.press("ask_broadcast")
    client.release("ask_broadcast")
    assert running.triggers == [("ask_broadcast", True), ("ask_broadcast", False)]


def test_status_reports_mute_state(running):
    client.mute(True)
    assert client.status()["muted"] is True


def test_missing_daemon_raises_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEHAIL_SOCKET", str(tmp_path / "absent.sock"))
    with pytest.raises(client.NotRunning):
        client.status()
