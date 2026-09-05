"""Settings window: microphone, output channels, routes and backend.

Edits are written back into the config file with `config.save_patch`, which merges into
whatever else is in the file. Channel and mic changes apply to the running pipeline
immediately; backend changes take effect on the next context.
"""

from __future__ import annotations

import logging
import subprocess

from PyQt6 import QtCore, QtWidgets

from .. import config as cfgmod
from ..capture.audio import input_devices
from ..config import Config, TtsChannel

log = logging.getLogger(__name__)

ACTIONS = ("ask_voice", "ask_screen", "ask_broadcast")


def pipewire_sinks() -> list[str]:
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=10
        ).stdout
        return [line.split("\t")[1] for line in out.splitlines() if "\t" in line]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        log.warning("could not list sinks: %s", exc)
        return []


class ChannelRow(QtWidgets.QWidget):
    """One output channel: where it goes, how loud, and a button to prove it works."""

    def __init__(self, channel: TtsChannel, sinks: list[str], on_test, parent=None):
        super().__init__(parent)
        self.channel = channel
        self._on_test = on_test

        self.enabled = QtWidgets.QCheckBox(channel.name)
        self.enabled.setChecked(channel.enabled)
        self.enabled.setMinimumWidth(90)

        self.target = QtWidgets.QComboBox()
        self.target.setEditable(True)
        self.target.addItem("default")
        self.target.addItems(sinks)
        self.target.setCurrentText(channel.target)
        self.target.setMinimumWidth(240)

        self.volume = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(channel.volume * 100))
        self.volume.setFixedWidth(110)
        self.vol_label = QtWidgets.QLabel(f"{int(channel.volume * 100)}%")
        self.vol_label.setFixedWidth(42)
        self.volume.valueChanged.connect(lambda v: self.vol_label.setText(f"{v}%"))

        self.app_name = QtWidgets.QLineEdit(channel.app_name)
        self.app_name.setPlaceholderText("application.name")
        self.app_name.setFixedWidth(150)
        self.app_name.setToolTip(
            "The PipeWire application.name this channel's audio carries.\n"
            "OpenWave binds an app source row to this name."
        )

        test = QtWidgets.QPushButton("Test")
        test.clicked.connect(lambda: self._on_test(self.values()))

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.enabled, self.target, self.volume, self.vol_label,
                       self.app_name, test):
            row.addWidget(widget)
        row.addStretch(1)

    def values(self) -> dict:
        return {
            "name": self.channel.name,
            "target": self.target.currentText().strip() or "default",
            "app_name": self.app_name.text().strip() or "gamepilot",
            "volume": round(self.volume.value() / 100, 2),
            "enabled": self.enabled.isChecked(),
            **({"voice_model": str(self.channel.voice_model)}
               if self.channel.voice_model else {}),
        }


class SettingsWindow(QtWidgets.QWidget):
    def __init__(self, cfg: Config, pipeline=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.pipeline = pipeline
        self.setWindowTitle("gamepilot settings")
        self.resize(820, 560)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._assistant_tab(), "Assistant")

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color: #7fc7ff;")

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.status)
        buttons.addStretch(1)
        save = QtWidgets.QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self.save)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.hide)
        buttons.addWidget(save)
        buttons.addWidget(close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addLayout(buttons)

    # -- tabs --------------------------------------------------------------
    def _audio_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        # microphone
        mic_box = QtWidgets.QGroupBox("Microphone")
        mic_form = QtWidgets.QFormLayout(mic_box)
        self.mic = QtWidgets.QComboBox()
        self.mic.addItem("system default", "")
        for name in input_devices():
            self.mic.addItem(name, name)
        idx = self.mic.findData(self.cfg.stt.input_device)
        self.mic.setCurrentIndex(idx if idx >= 0 else 0)
        mic_test = QtWidgets.QPushButton("Test (speak for 3s)")
        mic_test.clicked.connect(self._test_mic)
        mic_row = QtWidgets.QHBoxLayout()
        mic_row.addWidget(self.mic, 1)
        mic_row.addWidget(mic_test)
        mic_form.addRow("Input device", self._wrap(mic_row))
        self.mic_result = QtWidgets.QLabel("")
        self.mic_result.setWordWrap(True)
        mic_form.addRow("", self.mic_result)
        layout.addWidget(mic_box)

        # channels
        chan_box = QtWidgets.QGroupBox("Output channels")
        chan_layout = QtWidgets.QVBoxLayout(chan_box)
        header = QtWidgets.QLabel(
            "on   channel / PipeWire sink                       volume        "
            "application.name"
        )
        header.setStyleSheet("color: #8a94a6;")
        chan_layout.addWidget(header)
        sinks = pipewire_sinks()
        self.channel_rows = [ChannelRow(ch, sinks, self._test_channel)
                             for ch in self.cfg.tts.channels]
        for row in self.channel_rows:
            chan_layout.addWidget(row)
        hint = QtWidgets.QLabel(
            "Send a channel to <b>openwave_chat_mix</b> and everyone on voice comms "
            "hears it — Discord captures Monitor of OpenWave Chat Mix."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8a94a6;")
        chan_layout.addWidget(hint)
        layout.addWidget(chan_box)

        # routes
        route_box = QtWidgets.QGroupBox("Which hotkey speaks where")
        route_grid = QtWidgets.QGridLayout(route_box)
        self.route_boxes: dict[str, dict[str, QtWidgets.QCheckBox]] = {}
        keys = {
            "ask_voice": self.cfg.hotkeys.ask_voice,
            "ask_screen": self.cfg.hotkeys.ask_screen,
            "ask_broadcast": self.cfg.hotkeys.ask_broadcast,
        }
        for col, ch in enumerate(self.cfg.tts.channels):
            route_grid.addWidget(QtWidgets.QLabel(f"<b>{ch.name}</b>"), 0, col + 1)
        for row_i, action in enumerate(ACTIONS, start=1):
            label = QtWidgets.QLabel(f"{action}  <span style='color:#8a94a6'>"
                                     f"{keys.get(action) or '—'}</span>")
            route_grid.addWidget(label, row_i, 0)
            self.route_boxes[action] = {}
            current = self.cfg.tts.routes.get(action, [])
            for col, ch in enumerate(self.cfg.tts.channels):
                box = QtWidgets.QCheckBox()
                box.setChecked(ch.name in current)
                route_grid.addWidget(box, row_i, col + 1)
                self.route_boxes[action][ch.name] = box
        route_grid.setColumnStretch(len(self.cfg.tts.channels) + 1, 1)
        layout.addWidget(route_box)

        self.tts_enabled = QtWidgets.QCheckBox("Speak answers")
        self.tts_enabled.setChecked(self.cfg.tts.enabled)
        layout.addWidget(self.tts_enabled)
        layout.addStretch(1)
        return page

    def _assistant_tab(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)

        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["persistent", "oneshot"])
        self.mode.setCurrentText(self.cfg.backend.mode)
        self.mode.setToolTip("persistent keeps one claude process warm (~1s follow-ups)")
        form.addRow("Backend mode", self.mode)

        self.model = QtWidgets.QComboBox()
        self.model.setEditable(True)
        self.model.addItems(["sonnet", "opus", "haiku"])
        self.model.setCurrentText(self.cfg.backend.model)
        form.addRow("Model", self.model)

        self.effort = QtWidgets.QComboBox()
        self.effort.addItems(["low", "medium", "high"])
        self.effort.setCurrentText(self.cfg.backend.effort or "low")
        form.addRow("Effort", self.effort)

        self.screen_enabled = QtWidgets.QCheckBox(
            "Attach a screenshot on the screenshot hotkey")
        self.screen_enabled.setChecked(self.cfg.screen.enabled)
        form.addRow("Screenshots", self.screen_enabled)

        self.overlay_enabled = QtWidgets.QCheckBox("Show the on-screen overlay")
        self.overlay_enabled.setChecked(self.cfg.overlay.enabled)
        form.addRow("Overlay", self.overlay_enabled)

        keys = QtWidgets.QLabel(
            f"talk: <b>{self.cfg.hotkeys.ask_voice}</b> &nbsp; "
            f"screenshot: <b>{self.cfg.hotkeys.ask_screen}</b> &nbsp; "
            f"broadcast: <b>{self.cfg.hotkeys.ask_broadcast}</b> &nbsp; "
            f"cancel: <b>{self.cfg.hotkeys.cancel}</b><br>"
            "<span style='color:#8a94a6'>Rebind in the config file; find key names "
            "with <tt>gamepilot keys</tt>.</span>"
        )
        keys.setWordWrap(True)
        form.addRow("Hotkeys", keys)

        base = self.cfg.path or cfgmod.DEFAULT_CONFIG_PATH
        note = QtWidgets.QLabel(
            f"Saving to <tt>{cfgmod.local_path_for(base).name}</tt>, which is applied on "
            f"top of <tt>{base.name}</tt> (profile <b>{self.cfg.profile}</b>) so your "
            "hand-written config and its comments stay untouched."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8a94a6;")
        form.addRow("", note)
        return page

    @staticmethod
    def _wrap(layout) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        holder.setLayout(layout)
        return holder

    # -- actions -----------------------------------------------------------
    def _test_channel(self, values: dict) -> None:
        """Speak a phrase through one channel using the values currently on screen."""
        from ..tts import Speaker

        channel = TtsChannel(**{**values, "enabled": True,
                                "voice_model": self.cfg.tts.voice_model
                                if "voice_model" not in values else values["voice_model"]})
        probe_cfg = cfgmod.replace(self.cfg.tts, channels=[channel], enabled=True)
        speaker = Speaker(probe_cfg)
        if not speaker.available:
            self.status.setText("no voice model — run ./scripts/fetch-voice.sh")
            return
        speaker.begin([channel.name])
        speaker.feed(f"gamepilot on channel {channel.name}. ")
        speaker.flush()
        self.status.setText(f"testing {channel.name} → {channel.target}")

    def _test_mic(self) -> None:
        self.mic_result.setText("recording…")
        QtWidgets.QApplication.processEvents()

        device = self.mic.currentData() or None
        from ..capture.audio import Recorder

        rec = Recorder(self.cfg.stt.samplerate, 5.0, device)
        try:
            rec.start()
        except Exception as exc:  # noqa: BLE001 - bad device is user-visible, not fatal
            self.mic_result.setText(f"could not open device: {exc}")
            return
        QtCore.QTimer.singleShot(3000, lambda: self._finish_mic_test(rec))

    def _finish_mic_test(self, rec) -> None:
        audio = rec.stop()
        if audio.size == 0:
            self.mic_result.setText("no audio captured")
            return
        peak = float(abs(audio).max())
        self.mic_result.setText(f"peak {peak:.2f} — transcribing…")
        QtWidgets.QApplication.processEvents()
        try:
            transcriber = self.pipeline.transcriber if self.pipeline else None
            if transcriber is None:
                from ..stt import Transcriber

                transcriber = Transcriber(self.cfg.stt)
            text = transcriber.transcribe(audio)
        except Exception as exc:  # noqa: BLE001
            self.mic_result.setText(f"peak {peak:.2f} — transcription failed: {exc}")
            return
        self.mic_result.setText(f"peak {peak:.2f} — heard: {text or '(nothing)'}")

    def save(self) -> None:
        channels = [row.values() for row in self.channel_rows]
        routes = {
            action: [name for name, box in boxes.items() if box.isChecked()]
            for action, boxes in self.route_boxes.items()
        }
        patch = {
            "stt": {"input_device": self.mic.currentData() or ""},
            "tts": {
                "enabled": self.tts_enabled.isChecked(),
                "channels": channels,
                "routes": routes,
            },
            "backend": {
                "mode": self.mode.currentText(),
                "model": self.model.currentText().strip(),
                "effort": self.effort.currentText(),
            },
            "screen": {"enabled": self.screen_enabled.isChecked()},
            "overlay": {"enabled": self.overlay_enabled.isChecked()},
        }
        path = cfgmod.save_patch(patch, self.cfg.path)
        self._apply_live(channels, routes)
        self.status.setText(f"saved to {path.name} — overrides the main config")

    def _apply_live(self, channels: list[dict], routes: dict[str, list[str]]) -> None:
        """Push what can change without a restart into the running pipeline."""
        self.cfg.tts.channels = [
            TtsChannel(**{**ch, "voice_model": self.cfg.tts.channels[i].voice_model})
            if i < len(self.cfg.tts.channels) else TtsChannel(**ch)
            for i, ch in enumerate(channels)
        ]
        self.cfg.tts.routes = routes
        self.cfg.tts.enabled = self.tts_enabled.isChecked()
        self.cfg.stt.input_device = self.mic.currentData() or ""
        self.cfg.screen.enabled = self.screen_enabled.isChecked()
        self.cfg.backend.mode = self.mode.currentText()
        self.cfg.backend.model = self.model.currentText().strip()
        self.cfg.backend.effort = self.effort.currentText()
        if self.pipeline:
            self.pipeline.recorder.device = self.cfg.stt.input_device or None
