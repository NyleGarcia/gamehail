"""System tray icon: status at a glance, quick toggles, and a way into settings."""

from __future__ import annotations

import logging
from queue import Queue

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import Config
from .settings import SettingsWindow

log = logging.getLogger(__name__)

_IDLE = QtGui.QColor("#7fc7ff")
_BUSY = QtGui.QColor("#ffd166")
_TALK = QtGui.QColor("#8ce99a")


def make_icon(colour: QtGui.QColor) -> QtGui.QIcon:
    """Draw the tray glyph rather than shipping a theme-dependent asset."""
    size = 64
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setBrush(QtGui.QBrush(colour))
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.drawEllipse(6, 6, size - 12, size - 12)
    painter.setPen(QtGui.QPen(QtGui.QColor("#10141a")))
    font = painter.font()
    font.setPixelSize(34)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pix.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "G")
    painter.end()
    return QtGui.QIcon(pix)


class Tray(QtCore.QObject):
    """Owns the tray icon and the settings window.

    State arrives on the same event queue the overlay drains, so worker threads never
    touch widgets.
    """

    def __init__(self, cfg: Config, events: Queue, pipeline=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.events = events
        self.pipeline = pipeline
        self._settings: SettingsWindow | None = None

        self.icon = QtWidgets.QSystemTrayIcon(make_icon(_IDLE))
        self.icon.setToolTip("gamehail — idle")
        self.icon.activated.connect(self._on_activated)
        self.icon.setContextMenu(self._menu())
        self.icon.show()

    # -- menu --------------------------------------------------------------
    def _menu(self) -> QtWidgets.QMenu:
        menu = QtWidgets.QMenu()

        self.status_action = menu.addAction("idle")
        self.status_action.setEnabled(False)
        menu.addSeparator()

        route_menu = menu.addMenu("Default answer route")
        group = QtGui.QActionGroup(route_menu)
        group.setExclusive(True)
        for action_name in ("ask_voice", "ask_broadcast"):
            names = self.cfg.tts.routes.get(action_name, [])
            item = route_menu.addAction(f"{action_name}: {', '.join(names) or '—'}")
            item.setCheckable(True)
            item.setChecked(action_name == "ask_voice")
            item.setEnabled(False)  # display only; edit routes in settings
            group.addAction(item)

        self.mute_action = menu.addAction("Mute speech")
        self.mute_action.setCheckable(True)
        self.mute_action.toggled.connect(self._on_mute)

        menu.addAction("Stop speaking", self._on_stop_speaking)
        menu.addSeparator()
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("New context", self._on_reset)
        menu.addSeparator()
        menu.addAction("Quit", lambda: self.events.put(("quit", "")))
        return menu

    # -- slots -------------------------------------------------------------
    def _on_activated(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self.open_settings()

    def _on_mute(self, muted: bool) -> None:
        if self.pipeline:
            self.pipeline.speaker.cfg.enabled = not muted
            if muted:
                self.pipeline.speaker.cancel()

    def _on_stop_speaking(self) -> None:
        if self.pipeline:
            self.pipeline.speaker.cancel()

    def _on_reset(self) -> None:
        if self.pipeline:
            self.pipeline.backend.reset()
            self.set_status("new context")

    def open_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(self.cfg, self.pipeline)
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    # -- state -------------------------------------------------------------
    def set_status(self, text: str) -> None:
        short = text if len(text) < 60 else text[:57] + "…"
        self.status_action.setText(short or "idle")
        self.icon.setToolTip(f"gamehail — {short}" if short else "gamehail")
        colour = _IDLE
        if text.startswith("listening"):
            colour = _TALK
        elif text and not text.startswith("idle"):
            colour = _BUSY
        self.icon.setIcon(make_icon(colour))

    def notify(self, title: str, body: str) -> None:
        if self.cfg.ui.show_answers_in_tray and body:
            self.icon.showMessage(title, body, make_icon(_IDLE), 6000)
