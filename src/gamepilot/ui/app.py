"""Qt entry point: owns the event pump that feeds the overlay and the tray."""

from __future__ import annotations

import logging
from queue import Empty, Queue

from PyQt6 import QtCore, QtWidgets

from ..config import Config
from .tray import Tray

log = logging.getLogger(__name__)


class Dispatcher(QtCore.QObject):
    """Single consumer of the pipeline event queue, running on the Qt thread."""

    def __init__(self, events: Queue, overlay=None, tray: Tray | None = None, parent=None):
        super().__init__(parent)
        self.events = events
        self.overlay = overlay
        self.tray = tray
        self._answer = ""
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.drain)
        self._timer.start(50)

    def drain(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except Empty:
                return
            if kind == "quit":
                QtWidgets.QApplication.quit()
                return
            if kind == "answer":
                self._answer = payload
            elif kind == "append":
                self._answer += payload
            if self.overlay is not None:
                self.overlay.handle(kind, payload)
            if self.tray is not None:
                if kind == "status":
                    self.tray.set_status(payload)
                elif kind == "answer" and payload:
                    self.tray.set_status("idle")
                    if self.overlay is None:
                        self.tray.notify("gamepilot", payload)
                elif kind == "hide":
                    self.tray.set_status("idle")


def run_ui(cfg: Config, events: Queue, pipeline=None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("gamepilot")
    app.setQuitOnLastWindowClosed(False)  # closing settings must not kill the daemon

    overlay = None
    if cfg.overlay.enabled:
        from ..overlay import Overlay

        overlay = Overlay(cfg.overlay)
        overlay.hide()

    tray = None
    if cfg.ui.tray:
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            tray = Tray(cfg, events, pipeline)
        else:
            log.warning("no system tray available on this desktop")

    Dispatcher(events, overlay, tray)
    return app.exec()


def run_settings(cfg: Config) -> int:
    """Open the settings window on its own, without the daemon."""
    from .settings import SettingsWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("gamepilot")
    window = SettingsWindow(cfg)
    window.show()
    return app.exec()
