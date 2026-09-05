"""Every channel must receive the whole sentence, however slowly it drinks.

Players consume at playback rate. Writing to them one after another left the second
one's stdin idle for the whole of the first playback - it exited, and the write that
finally came died with a broken pipe, so the answer was heard on one channel or none.
The fake player here reproduces that by sleeping before it reads.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from gamepilot.config import TtsChannel, TtsConfig
from gamepilot.tts import Speaker

PCM = b"\x01\x02" * 8000  # 32 KB, larger than a pipe buffer


@pytest.fixture
def speaker(tmp_path, monkeypatch):
    cfg = TtsConfig(
        voice_model=tmp_path / "voice.onnx",
        channels=[TtsChannel(name="me"), TtsChannel(name="squad", enabled=True)],
        routes={"ask_voice": ["me", "squad"]},
    )
    (tmp_path / "voice.onnx").write_bytes(b"stub")
    (tmp_path / "voice.onnx.json").write_text('{"audio": {"sample_rate": 22050}}')

    speaker = Speaker(cfg)
    monkeypatch.setattr(Speaker, "_synth", lambda self, text, voice: PCM)

    outputs = {}

    def fake_player(self, channel, rate):
        out = tmp_path / f"{channel.name}.raw"
        outputs[channel.name] = out
        # Sleep first: a player that does not read immediately is what broke this.
        return ["sh", "-c", f"sleep 0.6; cat > {out}"]

    monkeypatch.setattr(Speaker, "_player_cmd", fake_player)
    return speaker, outputs, tmp_path


def test_every_channel_receives_the_whole_sentence(speaker):
    spk, outputs, _tmp = speaker
    spk._say("three short sentences.", ("me", "squad"))

    assert set(outputs) == {"me", "squad"}
    for name, path in outputs.items():
        assert path.exists(), f"{name} never received audio"
        assert path.read_bytes() == PCM, f"{name} got a truncated stream"


def test_channels_play_at_the_same_time_not_one_after_the_other(speaker):
    spk, _outputs, _tmp = speaker
    started = time.monotonic()
    spk._say("timing check.", ("me", "squad"))
    elapsed = time.monotonic() - started
    # Each fake player sleeps 0.6s. Concurrently that is ~0.6s total; sequentially it
    # would be ~1.2s. Half a second of headroom keeps this from being flaky.
    assert elapsed < 1.1, f"channels appear to be served sequentially ({elapsed:.2f}s)"
