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
    if getattr(args, "no_tray", False):
        cfg.ui.tray = False
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
        if cfg.overlay.enabled or cfg.ui.tray:
            from .ui.app import run_ui

            return run_ui(cfg, events, pipe)
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
        channels = args.channel or (pipe.route_for("ask_broadcast") if args.broadcast else None)
        answer = pipe.ask(" ".join(args.question), images or None, channels=channels)
        print(answer)
        return 0 if answer else 1
    finally:
        pipe.close()


def cmd_channels(args) -> int:
    """Show the configured audio channels and the PipeWire sinks they can target."""
    import subprocess

    cfg = _load(args)
    sinks = set()
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=10
        ).stdout
        sinks = {line.split("\t")[1] for line in out.splitlines() if "\t" in line}
    except (OSError, subprocess.SubprocessError, IndexError):
        pass

    print(f"player: {cfg.tts.player}")
    print("channels:")
    for ch in cfg.tts.channels:
        voice = ch.voice_model or cfg.tts.voice_model
        state = "on " if ch.enabled else "off"
        known = "" if ch.target in sinks or ch.target == "default" else "  [sink not found]"
        print(f"  {state} {ch.name:<10} -> {ch.target:<24} app={ch.app_name} "
              f"vol={ch.volume}{known}")
        if ch.voice_model:
            print(f"      voice: {voice}")
    print("routes:")
    for action, names in cfg.tts.routes.items():
        print(f"  {action:<14} -> {', '.join(names)}")
    if sinks:
        print("available sinks:")
        for name in sorted(sinks):
            print(f"  {name}")
    return 0


def cmd_say(args) -> int:
    """Speak a phrase on the given channels - use it to check the routing."""
    cfg = _load(args)
    from .tts import Speaker

    speaker = Speaker(cfg.tts)
    if not speaker.available:
        print("tts unavailable: check piper is installed and a voice model exists",
              file=sys.stderr)
        return 1
    route = args.channel or list(cfg.tts.routes.get("ask_broadcast", ["me"]))
    speaker.begin(route)
    speaker.feed(" ".join(args.text))
    speaker.flush()
    print(f"speaking on: {', '.join(route)}")
    import time

    while not speaker._queue.empty() or speaker._procs:
        time.sleep(0.1)
    time.sleep(0.5)
    speaker.close()
    return 0


def cmd_ctl(args) -> int:
    """Drive a running daemon over its control socket."""
    import json as _json

    from . import ipc

    msg: dict = {"cmd": args.command}
    if args.command in ("press", "release"):
        msg["action"] = args.arg[0] if args.arg else "ask_voice"
    elif args.command == "ask":
        msg["text"] = " ".join(args.arg)
        if args.route:
            msg["route"] = args.route
        if args.channel:
            msg["channels"] = args.channel
    elif args.command == "mute":
        msg["on"] = not (args.arg and args.arg[0] in ("off", "false", "0"))

    try:
        reply = ipc.send(msg, Path(args.socket) if args.socket else None)
    except (OSError, ConnectionError) as exc:
        print(f"no running gamepilot on the control socket: {exc}", file=sys.stderr)
        return 1
    print(_json.dumps(reply, indent=2) if args.json else
          reply.get("error") or reply.get("state") or "ok")
    return 0 if reply.get("ok") else 1


def cmd_settings(args) -> int:
    """Open the settings window without starting the daemon."""
    from .ui.app import run_settings

    return run_settings(_load(args))


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
    p_run.add_argument("--no-tray", action="store_true", help="run without the tray icon")
    p_run.set_defaults(func=cmd_run)

    p_ask = sub.add_parser("ask", help="ask one typed question (no mic)")
    _common(p_ask)
    p_ask.add_argument("question", nargs="+")
    p_ask.add_argument("--image", action="append", help="attach an image path (repeatable)")
    p_ask.add_argument("--channel", action="append",
                       help="speak on this audio channel (repeatable; default: the "
                            "ask_voice route)")
    p_ask.add_argument("--broadcast", action="store_true",
                       help="use the ask_broadcast route (you + the squad)")
    p_ask.add_argument("--no-tts", action="store_true")
    p_ask.set_defaults(func=cmd_ask)

    p_chan = sub.add_parser("channels", help="show audio channels, routes and sinks")
    _common(p_chan)
    p_chan.set_defaults(func=cmd_channels)

    p_say = sub.add_parser("say", help="speak a test phrase on given channels")
    _common(p_say)
    p_say.add_argument("text", nargs="+")
    p_say.add_argument("--channel", action="append", help="channel name (repeatable)")
    p_say.set_defaults(func=cmd_say)

    p_ctl = sub.add_parser("ctl", help="drive a running daemon (for decks and scripts)")
    p_ctl.add_argument("command",
                       choices=["press", "release", "ask", "cancel", "reset", "mute",
                                "status"])
    p_ctl.add_argument("arg", nargs="*", help="action name, question text, or on/off")
    p_ctl.add_argument("--route", help="route name for `ask` (default ask_voice)")
    p_ctl.add_argument("--channel", action="append", help="explicit channel (repeatable)")
    p_ctl.add_argument("--socket", help="control socket path")
    p_ctl.add_argument("--json", action="store_true", help="print the raw reply")
    p_ctl.set_defaults(func=cmd_ctl)

    p_set = sub.add_parser("settings", help="open the settings window")
    _common(p_set)
    p_set.set_defaults(func=cmd_settings)

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
