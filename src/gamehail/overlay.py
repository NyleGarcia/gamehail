"""Frameless always-on-top answer overlay (Qt).

Wayland gives no reliable client-side "stay on top", so the window is marked as a
tooltip-type surface and KWin is asked to keep it above via a window rule (see
scripts/install-kwin-rule.sh). The game must run in borderless-window mode for any
overlay to be visible on top of it.
"""

from __future__ import annotations

import logging
from PyQt6 import QtCore, QtGui, QtWidgets

from .config import OverlayConfig

log = logging.getLogger(__name__)

STYLE = """
#root {{ background: rgba(12, 14, 18, 235); border: 1px solid rgba(120, 190, 255, 90);
         border-radius: 10px; }}
#status {{ color: #7fc7ff; font-size: {small}px; font-weight: 600; }}
#body   {{ color: #e9eef5; font-size: {size}px; }}
"""


class Overlay(QtWidgets.QWidget):
    """Renders pipeline events. Driven by the UI dispatcher, never by worker threads."""

    def __init__(self, cfg: OverlayConfig):
        super().__init__(
            None,
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool,
        )
        self.cfg = cfg
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(cfg.opacity)
        self.setWindowTitle("gamehail-overlay")

        root = QtWidgets.QFrame(self)
        root.setObjectName("root")
        self.status = QtWidgets.QLabel("", root)
        self.status.setObjectName("status")
        self.body = QtWidgets.QLabel("", root)
        self.body.setObjectName("body")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)

        inner = QtWidgets.QVBoxLayout(root)
        inner.setContentsMargins(16, 12, 16, 14)
        inner.setSpacing(6)
        inner.addWidget(self.status)
        inner.addWidget(self.body)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        self.setStyleSheet(STYLE.format(size=cfg.font_size, small=max(10, cfg.font_size - 4)))
        self.setFixedWidth(cfg.width)

        self._hide_timer = QtCore.QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    # -- placement ---------------------------------------------------------
    def _place(self) -> None:
        screen = QtGui.QGuiApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        m = self.cfg.margin
        self.adjustSize()
        w, h = self.width(), self.height()
        x = area.left() + m if "left" in self.cfg.corner else area.right() - w - m
        y = area.top() + m if "top" in self.cfg.corner else area.bottom() - h - m
        self.move(x, y)

    # -- events ------------------------------------------------------------
    def handle(self, kind: str, payload: str) -> None:
        if kind == "status":
            self.show_status(payload)
        elif kind == "append":
            self.body.setText(self.body.text() + payload)
            self._place()
        elif kind == "answer":
            self.show_answer(payload)
        elif kind == "hide":
            self.hide()

    def show_status(self, text: str) -> None:
        self._hide_timer.stop()
        self.status.setText(text)
        self.body.setText("")
        self._place()
        self.show()

    def show_answer(self, text: str) -> None:
        self.status.setText("gamehail")
        if text:
            self.body.setText(text)
        self._place()
        self.show()
        if self.cfg.hide_after_s > 0:
            self._hide_timer.start(int(self.cfg.hide_after_s * 1000))
