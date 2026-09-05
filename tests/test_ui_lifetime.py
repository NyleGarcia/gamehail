"""The Qt event pump must survive garbage collection.

An unparented QObject assigned to nothing is collected the moment the line returns,
taking its QTimer with it - which silently stops the overlay, the tray, and (because no
Python runs during app.exec()) signal handling. This test pins that down.
"""

from __future__ import annotations

import gc
import os
from queue import Queue

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402

from gamehail.ui.app import Dispatcher  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_dispatcher_survives_collection_when_parented(app):
    events: Queue = Queue()
    Dispatcher(events, None, None, parent=app)
    gc.collect()
    survivors = [c for c in app.children() if isinstance(c, Dispatcher)]
    assert survivors, "dispatcher was collected; its timer would stop with it"
    assert survivors[0]._timer.isActive()
    for child in survivors:
        child.setParent(None)


def test_dispatcher_drains_events_to_the_overlay(app):
    class FakeOverlay:
        def __init__(self):
            self.seen = []

        def handle(self, kind, payload):
            self.seen.append((kind, payload))

    events: Queue = Queue()
    overlay = FakeOverlay()
    dispatcher = Dispatcher(events, overlay, None, parent=app)
    events.put(("status", "listening"))
    events.put(("append", "hello"))
    dispatcher.drain()
    assert overlay.seen == [("status", "listening"), ("append", "hello")]
    dispatcher.setParent(None)
