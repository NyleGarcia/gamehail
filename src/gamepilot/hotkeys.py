"""Global hotkeys via evdev.

KDE's own shortcuts fire once on press, which cannot express "hold to talk", and
Wayland has no client-side global grab at all. Reading /dev/input directly gives both
press and release, works while a fullscreen game has focus, and does not steal the key
from the game (the devices are read, never grabbed).

Requires read access to /dev/input/event*, i.e. membership of the `input` group.
"""

from __future__ import annotations

import logging
import selectors
import threading
from typing import Callable

from .config import HotkeyConfig

log = logging.getLogger(__name__)

Handler = Callable[[str, bool], None]  # (action, pressed)


class HotkeyListener:
    def __init__(self, cfg: HotkeyConfig, handler: Handler):
        self.cfg = cfg
        self.handler = handler
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # An empty key name disables that action.
        self._keymap = {
            key: action
            for key, action in (
                (cfg.ask_voice, "ask_voice"),
                (cfg.ask_screen, "ask_screen"),
                (cfg.ask_broadcast, "ask_broadcast"),
                (cfg.cancel, "cancel"),
            )
            if key
        }

    # -- device discovery --------------------------------------------------
    def _devices(self):
        from evdev import InputDevice, ecodes, list_devices

        wanted_codes = set()
        for name in self._keymap:
            code = getattr(ecodes, name, None)
            if code is None:
                raise ValueError(f"unknown key name in config: {name!r} (expected e.g. KEY_F13)")
            wanted_codes.add(code)

        paths = self.cfg.devices or list_devices()
        found = []
        for path in paths:
            try:
                dev = InputDevice(path)
            except (PermissionError, OSError) as exc:
                log.debug("skip %s: %s", path, exc)
                continue
            keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
            if self.cfg.devices or wanted_codes & keys:
                found.append(dev)
            else:
                dev.close()
        if not found:
            raise RuntimeError(
                "no input device exposes the configured hotkeys. "
                "Check `gamepilot devices`, and that you are in the `input` group."
            )
        for dev in found:
            log.info("listening on %s (%s)", dev.path, dev.name)
        return found

    # -- loop --------------------------------------------------------------
    def _run(self) -> None:
        from evdev import categorize, ecodes

        try:
            devices = self._devices()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, then exit thread
            log.error("hotkey listener failed to start: %s", exc)
            return

        sel = selectors.DefaultSelector()
        for dev in devices:
            sel.register(dev, selectors.EVENT_READ)

        code_to_action = {}
        for key_name, action in self._keymap.items():
            code_to_action[getattr(ecodes, key_name)] = action

        try:
            while not self._stop.is_set():
                for key, _mask in sel.select(timeout=0.5):
                    dev = key.fileobj
                    try:
                        for event in dev.read():  # type: ignore[union-attr]
                            if event.type != ecodes.EV_KEY:
                                continue
                            action = code_to_action.get(event.code)
                            if action is None or event.value == 2:  # ignore autorepeat
                                continue
                            self.handler(action, event.value == 1)
                    except OSError as exc:
                        log.warning("input device %s dropped: %s", dev, exc)
                        sel.unregister(dev)
        finally:
            sel.close()
            for dev in devices:
                dev.close()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def list_input_devices() -> list[tuple[str, str]]:
    from evdev import InputDevice, list_devices

    out = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except (PermissionError, OSError) as exc:
            out.append((path, f"<unreadable: {exc.__class__.__name__}>"))
            continue
        out.append((path, dev.name))
        dev.close()
    return out
