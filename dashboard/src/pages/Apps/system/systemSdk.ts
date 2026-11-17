/**
 * Restricted SDK injected into platform system apps.
 *
 * It deliberately exposes only `query`, `action` and `openManager`: system
 * apps have no rows, no external fetch and no chat/game actions. Every call
 * still goes through the host bridge, so the iframe never sees a JWT.
 */
export const SYSTEM_APP_SDK = `(function () {
  var pending = new Map();
  var seq = 0;
  var TIMEOUT_MS = 30000;
  var find = Document.prototype.querySelector;
  function intact() {
    try {
      return !find.call(document, "iframe,frame,object,embed,portal");
    } catch (e) {
      return false;
    }
  }
  function call(method, params) {
    if (!intact()) {
      return Promise.reject(new Error("Sandbox integrity check failed"));
    }
    return new Promise(function (resolve, reject) {
      var id = ++seq;
      var entry = { resolve: resolve, reject: reject, timer: null };
      pending.set(id, entry);
      entry.timer = setTimeout(function () {
        pending.delete(id);
        reject(new Error("Request timed out"));
      }, TIMEOUT_MS);
      parent.postMessage(
        {
          source: "octop-app",
          type: "request",
          id: id,
          method: method,
          params: params || {}
        },
        "*"
      );
    });
  }
  window.addEventListener("message", function (event) {
    var data = event.data;
    if (!data || data.source !== "octop-app-host") return;
    var entry = pending.get(data.id);
    if (!entry) return;
    pending.delete(data.id);
    if (entry.timer) clearTimeout(entry.timer);
    if (data.ok) entry.resolve(data.data);
    else entry.reject(new Error(data.error || "Request failed"));
  });
  window.octopSystem = {
    locale: window.__OCTOP_LOCALE__ || "zh",
    query: function (key, params) {
      return call("systemQuery", { name: key || "", args: params || {} });
    },
    action: function (key, params) {
      return call("systemAction", { name: key || "", args: params || {} });
    },
    openManager: function (key) {
      return call("openManager", { name: key || "" });
    }
  };
})();`;
