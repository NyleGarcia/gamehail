"""Settings window: microphone, output channels, routes and backend.

Edits are written back into the config file with `config.save_patch`, which merges into
whatever else is in the file. Channel and mic changes apply to the running pipeline
immediately; backend changes take effect on the next context.
"""

from __future__ import annotations

import logging
import subprocess
import threading

from PyQt6 import QtCore, QtWidgets

from .. import config as cfgmod
from .. import voices as voicelib
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

    def __init__(self, channel: TtsChannel, sinks: list[str], on_test,
                 installed: list | None = None, parent=None):
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

        self.voice = QtWidgets.QComboBox()
        self.voice.addItem("default voice", "")
        for path in installed or []:
            self.voice.addItem(path.stem, str(path))
        if channel.voice_model:
            index = self.voice.findData(str(channel.voice_model))
            if index < 0:
                self.voice.addItem(channel.voice_model.stem, str(channel.voice_model))
                index = self.voice.count() - 1
            self.voice.setCurrentIndex(index)
        self.voice.setMinimumWidth(170)
        self.voice.setToolTip("Give the broadcast channel a different voice and the "
                              "squad can tell it apart from you.")

        test = QtWidgets.QPushButton("Test")
        test.clicked.connect(lambda: self._on_test(self.values()))

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for widget in (self.enabled, self.target, self.volume, self.vol_label,
                       self.app_name, self.voice, test):
            row.addWidget(widget)
        row.addStretch(1)

    def values(self) -> dict:
        chosen = self.voice.currentData() or ""
        return {
            "name": self.channel.name,
            "target": self.target.currentText().strip() or "default",
            "app_name": self.app_name.text().strip() or "gamepilot",
            "volume": round(self.volume.value() / 100, 2),
            "enabled": self.enabled.isChecked(),
            **({"voice_model": chosen} if chosen else {}),
        }

    def refresh_voices(self, installed: list) -> None:
        current = self.voice.currentData()
        self.voice.clear()
        self.voice.addItem("default voice", "")
        for path in installed:
            self.voice.addItem(path.stem, str(path))
        index = self.voice.findData(current)
        self.voice.setCurrentIndex(max(index, 0))


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
            "application.name        voice"
        )
        header.setStyleSheet("color: #8a94a6;")
        chan_layout.addWidget(header)
        sinks = pipewire_sinks()
        self._installed = voicelib.installed()
        self.channel_rows = [ChannelRow(ch, sinks, self._test_channel, self._installed)
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

        layout.addWidget(self._voice_box())

        self.tts_enabled = QtWidgets.QCheckBox("Speak answers")
        self.tts_enabled.setChecked(self.cfg.tts.enabled)
        layout.addWidget(self.tts_enabled)
        layout.addStretch(1)
        return page

    def _voice_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Voice")
        form = QtWidgets.QFormLayout(box)

        self.voice = QtWidgets.QComboBox()
        self._fill_default_voice()
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.voice, 1)
        get_more = QtWidgets.QPushButton("Get more voices…")
        get_more.clicked.connect(self._open_voice_downloader)
        preview = QtWidgets.QPushButton("Preview")
        preview.clicked.connect(self._preview_voice)
        row.addWidget(preview)
        row.addWidget(get_more)
        form.addRow("Default voice", self._wrap(row))

        self.speed = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.speed.setRange(70, 160)  # length_scale 0.7 (fast) .. 1.6 (slow), inverted
        self.speed.setValue(int((self.cfg.tts.length_scale or 1.0) * 100))
        self.speed_label = QtWidgets.QLabel(f"{self.speed.value() / 100:.2f}×")
        self.speed.valueChanged.connect(
            lambda v: self.speed_label.setText(f"{v / 100:.2f}×"))
        speed_row = QtWidgets.QHBoxLayout()
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(self.speed_label)
        form.addRow("Pace (lower is faster)", self._wrap(speed_row))
        return box

    def _fill_default_voice(self) -> None:
        current = self.voice.currentData() if self.voice.count() else (
            str(self.cfg.tts.voice_model) if self.cfg.tts.voice_model else "")
        self.voice.clear()
        for path in self._installed:
            self.voice.addItem(path.stem, str(path))
        if not self._installed:
            self.voice.addItem("no voices installed", "")
        index = self.voice.findData(current)
        self.voice.setCurrentIndex(max(index, 0))

    def _preview_voice(self) -> None:
        chosen = self.voice.currentData()
        if not chosen:
            self.status.setText("no voice installed — use Get more voices…")
            return
        self._test_channel({
            "name": "preview", "target": "default", "app_name": "gamepilot",
            "volume": 1.0, "voice_model": chosen,
        })

    def _open_voice_downloader(self) -> None:
        dialog = VoiceDownloader(self)
        dialog.exec()
        self._installed = voicelib.installed()
        self._fill_default_voice()
        for row in self.channel_rows:
            row.refresh_voices(self._installed)

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
                "voice_model": self.voice.currentData() or "",
                "length_scale": round(self.speed.value() / 100, 2),
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
        from pathlib import Path as _Path

        self.cfg.tts.channels = [
            TtsChannel(**{**ch,
                          "voice_model": _Path(ch["voice_model"])
                          if ch.get("voice_model") else None})
            for ch in channels
        ]
        chosen = self.voice.currentData()
        self.cfg.tts.voice_model = _Path(chosen) if chosen else None
        self.cfg.tts.length_scale = round(self.speed.value() / 100, 2)
        self.cfg.tts.routes = routes
        self.cfg.tts.enabled = self.tts_enabled.isChecked()
        self.cfg.stt.input_device = self.mic.currentData() or ""
        self.cfg.screen.enabled = self.screen_enabled.isChecked()
        self.cfg.backend.mode = self.mode.currentText()
        self.cfg.backend.model = self.model.currentText().strip()
        self.cfg.backend.effort = self.effort.currentText()
        if self.pipeline:
            self.pipeline.recorder.device = self.cfg.stt.input_device or None


class VoiceDownloader(QtWidgets.QDialog):
    """Browse piper's published voices and fetch one.

    The catalogue is piper's own `voices.json`, so the list is what actually exists
    rather than a hardcoded guess. Downloads run on a worker thread; the dialog polls
    for progress so no Qt object is touched off the main thread.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Get voices")
        self.resize(460, 180)
        self._progress = (0.0, "")
        self._error: str | None = None
        self._worker: threading.Thread | None = None

        self.language = QtWidgets.QComboBox()
        self.language.addItems(["en", "de", "fr", "es", "it", "nl", "pl", "pt", "ru"])
        self.language.currentTextChanged.connect(self._fill)

        self.voices = QtWidgets.QComboBox()
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.label = QtWidgets.QLabel("")

        self.get = QtWidgets.QPushButton("Download")
        self.get.clicked.connect(self._download)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)

        form = QtWidgets.QFormLayout()
        form.addRow("Language", self.language)
        form.addRow("Voice", self.voices)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.label, 1)
        buttons.addWidget(self.get)
        buttons.addWidget(close)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.bar)
        layout.addLayout(buttons)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)
        self._fill("en")

    def _fill(self, language: str) -> None:
        self.voices.clear()
        keys = voicelib.available(language)
        if not keys:
            self.label.setText("catalogue unavailable — check the network")
            return
        installed = {p.stem for p in voicelib.installed()}
        for key in keys:
            self.voices.addItem(f"{key}{'  (installed)' if key in installed else ''}", key)

    def _download(self) -> None:
        key = self.voices.currentData()
        if not key or (self._worker and self._worker.is_alive()):
            return
        self.get.setEnabled(False)
        self._error = None

        def run():
            try:
                voicelib.download(key, progress=lambda f, m: setattr(self, "_progress", (f, m)))
            except Exception as exc:  # noqa: BLE001 - reported in the dialog
                self._error = str(exc)

        self._worker = threading.Thread(target=run, name="voice-download", daemon=True)
        self._worker.start()

    def _tick(self) -> None:
        fraction, message = self._progress
        self.bar.setValue(int(fraction * 100))
        if self._error:
            self.label.setText(f"failed: {self._error}")
            self.get.setEnabled(True)
            self._error = None
            return
        if message:
            self.label.setText(message)
        if self._worker and not self._worker.is_alive():
            self.get.setEnabled(True)
            self._worker = None
            self._fill(self.language.currentText())
