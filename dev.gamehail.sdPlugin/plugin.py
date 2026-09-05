"""gamehail plugin for OpenDeck / Stream Deck.

One process. It speaks OpenDeck's WebSocket protocol and drives the gamehail daemon
through its control socket; nothing here needs to outlive the plugin.

The interesting part is *Hold to Ask*: a deck key sends keyDown and keyUp as separate
events, so it maps straight onto the daemon's press/release commands and behaves like a
held push-to-talk key — except it needs no `input` group and cannot collide with a game
binding.

Nothing may raise to the top level. OpenDeck does not restart a plugin that dies — the
keys simply stop responding, with no indication why — so every handler is wrapped and
every failure is logged and swallowed.
"""

import json
import logging
import os
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ghdeck import client                       # noqa: E402
from ghdeck.ws import WebSocket                 # noqa: E402

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG if os.environ.get("GAMEHAIL_DECK_DEBUG") else logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("gamehail-deck")


def _log_uncaught(kind, value, trace):
    """Anything that escapes must reach the log.

    OpenDeck discards a plugin's stderr, so an uncaught exception is otherwise a
    plugin that vanishes mid-session with an empty log and dead keys - which is
    exactly how this went wrong the first time.
    """
    log.critical("uncaught %s", kind.__name__, exc_info=(kind, value, trace))


sys.excepthook = _log_uncaught

ASK = "dev.gamehail.ask"
PRESET = "dev.gamehail.preset"
MUTE = "dev.gamehail.mute"
CANCEL = "dev.gamehail.cancel"
STATUS = "dev.gamehail.status"

ROUTE_LABEL = {
    "ask_voice": "Ask",
    "ask_screen": "Ask + shot",
    "ask_broadcast": "Squad",
}
POLL_SECONDS = 1.0


class Plugin:
    def __init__(self, ws, uuid):
        self.ws = ws
        self.uuid = uuid
        self.contexts = {}   # context -> {"action": uuid, "settings": {...}}
        self._last_poll = 0.0
        self._last_state = None

    # -- protocol helpers --------------------------------------------------
    def _send(self, payload):
        try:
            self.ws.send_json(payload)
        except (OSError, ConnectionError) as exc:
            log.error("send failed: %s", exc)

    def set_title(self, context, title):
        self._send({"event": "setTitle", "context": context,
                    "payload": {"title": title, "target": 0}})

    def set_state(self, context, state):
        self._send({"event": "setState", "context": context,
                    "payload": {"state": state}})

    def alert(self, context):
        self._send({"event": "showAlert", "context": context})

    def ok(self, context):
        self._send({"event": "showOk", "context": context})

    def request_settings(self, context):
        self._send({"event": "getSettings", "context": context})

    # -- key handling ------------------------------------------------------
    def on_key_down(self, action, context, settings):
        if action == ASK:
            route = settings.get("route", "ask_voice")
            client.press(route)
            self.set_title(context, "listening…")
        elif action == PRESET:
            text = (settings.get("text") or "").strip()
            if not text:
                self.alert(context)
                self.set_title(context, "set text")
                return
            client.ask(text, route=settings.get("route", "ask_voice"))
            self.ok(context)
        elif action == CANCEL:
            client.cancel()
            self.ok(context)
        elif action == MUTE:
            muted = bool(client.status().get("muted"))
            client.mute(not muted)
            self.set_state(context, 1 if not muted else 0)
        elif action == STATUS:
            # A press is a cheap way to force a refresh when curious.
            self.refresh_status(force=True)

    def on_key_up(self, action, context, settings):
        if action == ASK:
            route = settings.get("route", "ask_voice")
            client.release(route)
            self.set_title(context, "thinking…")

    # -- status polling ----------------------------------------------------
    def refresh_status(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_poll < POLL_SECONDS:
            return
        self._last_poll = now

        watchers = [c for c, meta in self.contexts.items()
                    if meta["action"] in (STATUS, ASK, MUTE)]
        if not watchers:
            return
        try:
            state = client.status()
        except client.NotRunning:
            for context in watchers:
                meta = self.contexts[context]
                if meta["action"] == STATUS:
                    self.set_title(context, "offline")
                elif meta["action"] == ASK:
                    self.set_title(context, "offline")
            self._last_state = None
            return
        except (OSError, ValueError) as exc:
            log.warning("status failed: %s", exc)
            return

        label = state.get("state") or "idle"
        for context, meta in self.contexts.items():
            if meta["action"] == STATUS:
                self.set_title(context, self._status_title(state))
            elif meta["action"] == ASK and label in ("idle", ""):
                route = meta["settings"].get("route", "ask_voice")
                self.set_title(context, ROUTE_LABEL.get(route, "Ask"))
            elif meta["action"] == MUTE:
                self.set_state(context, 1 if state.get("muted") else 0)
        self._last_state = label

    @staticmethod
    def _status_title(state):
        label = state.get("state") or "idle"
        if label.startswith("listening"):
            return "listening"
        if label.startswith("»"):
            return "thinking"
        if label in ("idle", ""):
            game = state.get("game_name") or state.get("game") or "no game"
            return f"{game}\n{state.get('model', '?')}"
        return label[:14]

    # -- event dispatch ----------------------------------------------------
    def handle(self, message):
        event = message.get("event")
        context = message.get("context")
        action = message.get("action")
        payload = message.get("payload") or {}
        settings = payload.get("settings") or {}

        if event in ("willAppear", "didReceiveSettings"):
            self.contexts[context] = {"action": action, "settings": settings}
            if action == ASK:
                self.set_title(context, ROUTE_LABEL.get(
                    settings.get("route", "ask_voice"), "Ask"))
            self.refresh_status(force=True)
        elif event == "willDisappear":
            self.contexts.pop(context, None)
        elif event == "keyDown":
            meta = self.contexts.setdefault(context, {"action": action, "settings": settings})
            meta["settings"] = settings or meta["settings"]
            self.on_key_down(action, context, meta["settings"])
        elif event == "keyUp":
            meta = self.contexts.get(context, {"action": action, "settings": settings})
            self.on_key_up(action, context, meta.get("settings", settings))
        elif event == "propertyInspectorDidAppear":
            self.request_settings(context)
        elif event == "sendToPlugin":
            self.on_pi_message(context, payload)

    def on_pi_message(self, context, payload):
        """The property inspector asks for live data - the daemon's routes and state."""
        if payload.get("request") != "state":
            return
        try:
            state = client.status()
            data = {"running": True, "routes": state.get("routes", {}),
                    "channels": state.get("channels", []),
                    "profile": state.get("profile", "")}
        except client.NotRunning as exc:
            data = {"running": False, "error": str(exc)}
        self._send({"event": "sendToPropertyInspector", "context": context,
                    "payload": data})


def main():
    # OpenDeck passes -port/-pluginUUID/-registerEvent/-info, in any order.
    args = {}
    argv = sys.argv[1:]
    for index in range(0, len(argv) - 1, 2):
        args[argv[index].lstrip("-")] = argv[index + 1]

    port = int(args.get("port", 0))
    uuid = args.get("pluginUUID", "")
    register = args.get("registerEvent", "registerPlugin")
    if not port:
        print("gamehail deck plugin: no -port given", file=sys.stderr)
        return 2

    ws = WebSocket(port, timeout=None)
    ws.send_json({"event": register, "uuid": uuid})
    log.info("registered with OpenDeck on port %s", port)

    plugin = Plugin(ws, uuid)
    while True:
        try:
            ready, _, _ = select.select([ws], [], [], POLL_SECONDS)
            if ready:
                raw = ws.receive()
                if raw:
                    try:
                        plugin.handle(json.loads(raw))
                    except Exception:  # noqa: BLE001 - a bad event must not kill us
                        log.exception("handler failed for: %.200s", raw)
            plugin.refresh_status()
        except ConnectionError as exc:
            log.info("host went away: %s", exc)
            return 0
        except OSError as exc:
            # select() and recv() on a socket the host has torn down raise plain
            # OSError, which is not a ConnectionError. Uncaught, it ended the process
            # silently; the keys then look placed but do nothing.
            log.info("socket error, exiting for a restart: %s", exc)
            return 0
        except Exception:  # noqa: BLE001
            log.exception("loop error")
            time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
