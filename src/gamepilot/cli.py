"""gamepilot command line."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from queue import Queue

from . import __version__, config
from .pipeline import Pipeline


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", type=Path, default=None, help="path to config.toml")
    p.add_argument("-P", "--profile", default=None, help="config profile to use")
    p.add_argument("--mode", choices=["persistent", "oneshot"], default=None,
                   help="override backend mode")
    p.add_argument("--model", default=None, help="override model (sonnet, opus, haiku, ...)")
    p.add_argument("-v", "--verbose", action="store_true")


def _load(args) -> config.Config:
    cfg = config.load(args.config, args.profile)
    if args.mode:
        cfg.backend.mode = args.mode
    if args.model:
        cfg.backend.model = args.model
    return cfg


def cmd_run(args) -> int:
    cfg = _load(args)
    events: Queue = Queue()
    pipe = Pipeline(cfg, events)

    if not args.no_warmup:
        try:
            pipe.transcriber.warmup()
        except Exception as exc:  # noqa: BLE001 - degraded, not fatal
            logging.error("STT warmup failed: %s", exc)

    pipe.start()
    logging.info(
        "gamepilot up | profile=%s backend=%s model=%s | voice=%s screen=%s cancel=%s",
        cfg.profile, cfg.backend.mode, cfg.backend.model,
        cfg.hotkeys.ask_voice, cfg.hotkeys.ask_screen, cfg.hotkeys.cancel,
    )

    def shutdown(*_a):
        events.put(("quit", ""))

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        if cfg.overlay.enabled:
            from .overlay import run_qt

            return run_qt(cfg.overlay, events)
        while True:  # headless: drain events so the queue cannot grow unbounded
            kind, payload = events.get()
            if kind == "quit":
                return 0
            if kind in ("status", "answer") and payload:
                print(payload, flush=True)
    finally:
        pipe.close()


def cmd_ask(args) -> int:
    """One-shot text question - handy for testing the backend without a mic."""
    cfg = _load(args)
    cfg.overlay.enabled = False
    if args.no_tts:
        cfg.tts.enabled = False
    pipe = Pipeline(cfg)
    try:
        images = [Path(p) for p in (args.image or [])]
        answer = pipe.ask(" ".join(args.question), images or None)
        print(answer)
        return 0 if answer else 1
    finally:
        pipe.close()


def cmd_devices(_args) -> int:
    from .hotkeys import list_input_devices

    for path, name in list_input_devices():
        print(f"{path}\t{name}")
    return 0


def cmd_keys(args) -> int:
    """Print evdev key names as you press them - use it to fill in the hotkey config."""
    from evdev import InputDevice, ecodes, list_devices
    import selectors

    sel = selectors.DefaultSelector()
    devs = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except (PermissionError, OSError):
            continue
        if ecodes.EV_KEY in dev.capabilities():
            sel.register(dev, selectors.EVENT_READ)
            devs.append(dev)
    if not devs:
        print("no readable input devices - are you in the `input` group?", file=sys.stderr)
        return 1
    print("press keys (ctrl-c to stop)…")
    try:
        while True:
            for key, _ in sel.select(timeout=1.0):
                for event in key.fileobj.read():
                    if event.type == ecodes.EV_KEY and event.value == 1:
                        name = ecodes.KEY.get(event.code, f"<{event.code}>")
                        print(f"{name}\t({key.fileobj.path})")
    except KeyboardInterrupt:
        return 0
    finally:
        for dev in devs:
            dev.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gamepilot", description=__doc__)
    parser.add_argument("--version", action="version", version=f"gamepilot {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="start the hotkey daemon")
    _common(p_run)
    p_run.add_argument("--no-warmup", action="store_true", help="skip preloading the STT model")
    p_run.set_defaults(func=cmd_run)

    p_ask = sub.add_parser("ask", help="ask one typed question (no mic)")
    _common(p_ask)
    p_ask.add_argument("question", nargs="+")
    p_ask.add_argument("--image", action="append", help="attach an image path (repeatable)")
    p_ask.add_argument("--no-tts", action="store_true")
    p_ask.set_defaults(func=cmd_ask)

    p_dev = sub.add_parser("devices", help="list input devices")
    p_dev.set_defaults(func=cmd_devices)

    p_keys = sub.add_parser("keys", help="print key names as you press them")
    p_keys.set_defaults(func=cmd_keys)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
