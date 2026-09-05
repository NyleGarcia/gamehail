// Connecting to OpenDeck, once, for every inspector in this plugin.
//
// Two conventions are in the wild and OpenDeck ships both: the Elgato SDK calls a
// global function with the connection details, while OpenDeck's own newer plugins
// await a global promise of the same tuple. Supporting both is a few lines and removes
// a whole class of "the panel is blank" bug. (Same approach as the OpenWave plugin.)
//
// The context to send on is inActionInfo.context — the ACTION's context, not the
// inspector's own uuid. Sending the uuid instead is accepted by the socket and routed
// nowhere: settings are never saved and the panel sits empty with nothing to explain it.
(function () {
  let socket = null;
  let context = null;
  let settings = {};
  let answered = false;
  const listeners = [];

  function start(port, uuid, registerEvent, _info, actionInfo) {
    const info = typeof actionInfo === "string" ? JSON.parse(actionInfo) : actionInfo;
    context = info.context;
    settings = (info.payload && info.payload.settings) || {};
    socket = new WebSocket("ws://127.0.0.1:" + port);
    socket.onopen = () => {
      socket.send(JSON.stringify({ event: registerEvent, uuid: uuid }));
      GP.ask();
      // Asking once is not enough: the socket can open before the plugin has finished
      // registering, and a reply that lands then is answered to nobody.
      let tries = 0;
      const retry = setInterval(() => {
        if (answered || ++tries > 6) { clearInterval(retry); return; }
        GP.ask();
      }, 600);
      listeners.forEach((fn) => fn(settings, null));
    };
    socket.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch (e) { return; }
      if (message.event === "sendToPropertyInspector") {
        answered = true;
        listeners.forEach((fn) => fn(settings, message.payload || {}));
      } else if (message.event === "didReceiveSettings") {
        settings = (message.payload && message.payload.settings) || {};
        listeners.forEach((fn) => fn(settings, null));
      }
    };
  }

  window.GP = {
    onReady(fn) { listeners.push(fn); if (socket && socket.readyState === 1) fn(settings, null); },
    get settings() { return settings; },
    save(patch) {
      settings = Object.assign({}, settings, patch);
      if (!socket || socket.readyState !== 1) return;
      socket.send(JSON.stringify({ event: "setSettings", context: context, payload: settings }));
    },
    ask() {
      if (!socket || socket.readyState !== 1) return;
      socket.send(JSON.stringify({
        event: "sendToPlugin", context: context, payload: { request: "state" },
      }));
    },
  };

  window.connectElgatoStreamDeckSocket = start;
  window.connectSocket = start;
  if (window.openDeckConnection && window.openDeckConnection.then) {
    window.openDeckConnection.then((a) => start.apply(null, a));
  }
})();
