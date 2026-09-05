"""Qt entry point: owns the event pump that feeds the overlay and the tray."""

from __future__ import annotations

import logging
import signal
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
                        self.tray.notify("gamehail", payload)
                elif kind == "hide":
                    self.tray.set_status("idle")


def run_ui(cfg: Config, events: Queue, pipeline=None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("gamehail")
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

    # Parented to the application: an unparented QObject assigned to nothing is
    # collected as soon as this line returns, and its QTimer goes with it. That leaves
    # the event queue unpolled - no overlay, no tray updates - and, because nothing
    # Python-side ever runs during app.exec(), no signal handling either.
    dispatcher = Dispatcher(events, overlay, tray, parent=app)
    assert dispatcher.parent() is app

    # Python only runs signal handlers when it has the interpreter, which inside
    # app.exec() happens on the dispatcher's timer. Installing them here - after the
    # QApplication exists - means SIGTERM from `systemctl stop` quits the loop instead
    # of waiting for the kill that follows the stop timeout.
    def _quit(_signum, _frame):
        events.put(("quit", ""))

    signal.signal(signal.SIGTERM, _quit)
    signal.signal(signal.SIGINT, _quit)
    return app.exec()


def run_settings(cfg: Config) -> int:
    """Open the settings window on its own, without the daemon."""
    from .settings import SettingsWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("gamehail")
    window = SettingsWindow(cfg)
    window.show()
    return app.exec()
