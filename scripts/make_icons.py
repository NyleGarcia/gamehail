"""Draw the deck plugin's key icons.

Generated rather than hand-drawn so every key shares one palette and shape language,
and so a tweak is a code change instead of seven image edits. Run:

    uv run python scripts/make_icons.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtGui  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "dev.gamepilot.sdPlugin" / "icons"
BG = QtGui.QColor("#151a21")
ACCENT = QtGui.QColor("#7fc7ff")
WARM = QtGui.QColor("#ffd166")
GREEN = QtGui.QColor("#8ce99a")
RED = QtGui.QColor("#ff8787")
INK = QtGui.QColor("#e9eef5")


def canvas(size: int) -> tuple[QtGui.QPixmap, QtGui.QPainter]:
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setBrush(QtGui.QBrush(BG))
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, size, size, size * 0.18, size * 0.18)
    return pix, painter


def pen(painter, colour, width, cap=QtCore.Qt.PenCapStyle.RoundCap):
    stroke = QtGui.QPen(colour, width)
    stroke.setCapStyle(cap)
    stroke.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    painter.setPen(stroke)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)


def mic(painter, s, colour):
    pen(painter, colour, s * 0.07)
    painter.setBrush(QtGui.QBrush(colour))
    painter.drawRoundedRect(QtCore.QRectF(s * 0.40, s * 0.20, s * 0.20, s * 0.34),
                            s * 0.10, s * 0.10)
    pen(painter, colour, s * 0.07)
    painter.drawArc(QtCore.QRectF(s * 0.30, s * 0.34, s * 0.40, s * 0.34), 180 * 16, 180 * 16)
    painter.drawLine(QtCore.QPointF(s * 0.5, s * 0.68), QtCore.QPointF(s * 0.5, s * 0.80))


def speaker(painter, s, colour):
    painter.setBrush(QtGui.QBrush(colour))
    pen(painter, colour, s * 0.06)
    body = QtGui.QPolygonF([
        QtCore.QPointF(s * 0.24, s * 0.40), QtCore.QPointF(s * 0.38, s * 0.40),
        QtCore.QPointF(s * 0.52, s * 0.26), QtCore.QPointF(s * 0.52, s * 0.74),
        QtCore.QPointF(s * 0.38, s * 0.60), QtCore.QPointF(s * 0.24, s * 0.60),
    ])
    painter.drawPolygon(body)


def waves(painter, s, colour):
    pen(painter, colour, s * 0.06)
    for index, radius in enumerate((0.14, 0.24)):
        rect = QtCore.QRectF(s * (0.52 - radius), s * (0.5 - radius),
                             s * radius * 2, s * radius * 2)
        painter.drawArc(rect, -55 * 16, 110 * 16)


def draw(name: str, glyph) -> None:
    for size, suffix in ((72, ""), (144, "@2x")):
        pix, painter = canvas(size)
        glyph(painter, size)
        painter.end()
        pix.save(str(OUT / f"{name}{suffix}.png"), "PNG")


def glyph_plugin(painter, s):
    painter.setPen(QtGui.QPen(ACCENT))
    font = painter.font()
    font.setPixelSize(int(s * 0.62))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QtCore.QRectF(0, 0, s, s), QtCore.Qt.AlignmentFlag.AlignCenter, "G")


def glyph_ask(painter, s):
    mic(painter, s, ACCENT)


def glyph_preset(painter, s):
    pen(painter, WARM, s * 0.075)
    for row, width in enumerate((0.46, 0.34, 0.42)):
        y = s * (0.34 + row * 0.16)
        painter.drawLine(QtCore.QPointF(s * 0.27, y), QtCore.QPointF(s * (0.27 + width), y))


def glyph_speaking(painter, s):
    speaker(painter, s, GREEN)
    waves(painter, s, GREEN)


def glyph_mute(painter, s):
    speaker(painter, s, RED)
    pen(painter, RED, s * 0.07)
    painter.drawLine(QtCore.QPointF(s * 0.60, s * 0.38), QtCore.QPointF(s * 0.80, s * 0.62))
    painter.drawLine(QtCore.QPointF(s * 0.80, s * 0.38), QtCore.QPointF(s * 0.60, s * 0.62))


def glyph_cancel(painter, s):
    pen(painter, RED, s * 0.09)
    painter.drawLine(QtCore.QPointF(s * 0.32, s * 0.32), QtCore.QPointF(s * 0.68, s * 0.68))
    painter.drawLine(QtCore.QPointF(s * 0.68, s * 0.32), QtCore.QPointF(s * 0.32, s * 0.68))


def glyph_status(painter, s):
    pen(painter, ACCENT, s * 0.07)
    painter.drawEllipse(QtCore.QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56))
    painter.setBrush(QtGui.QBrush(ACCENT))
    painter.drawEllipse(QtCore.QRectF(s * 0.44, s * 0.44, s * 0.12, s * 0.12))


def main() -> int:
    # QPixmap needs a live QGuiApplication even when nothing is shown.
    app = QApplication.instance() or QApplication([])
    _ = app
    OUT.mkdir(parents=True, exist_ok=True)
    for name, glyph in (
        ("plugin", glyph_plugin), ("ask", glyph_ask), ("preset", glyph_preset),
        ("speaking", glyph_speaking), ("mute", glyph_mute), ("cancel", glyph_cancel),
        ("status", glyph_status),
    ):
        draw(name, glyph)
    print(f"wrote {len(list(OUT.glob('*.png')))} icons to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
