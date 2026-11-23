import { APP_CSP } from "./appCsp";

/**
 * Bridge exposed to app HTML as `window.octopApp`. Every call goes through the
 * parent dashboard over postMessage so the iframe never sees a JWT and never
 * needs `allow-same-origin`.
 */
export const APP_SDK = `(function () {
  var pending = new Map();
  var seq = 0;
  var TIMEOUT_MS = 30000;
  var dataListeners = [];
  // Captured before any app script runs; the freeze installs first.
  var find = Document.prototype.querySelector;
  function intact() {
    try {
      if (typeof window.RTCPeerConnection !== "undefined") return false;
      if (typeof window.webkitRTCPeerConnection !== "undefined") return false;
      if (typeof window.mozRTCPeerConnection !== "undefined") return false;
      return !find.call(document, "iframe,frame,object,embed,portal");
    } catch (e) {
      return false;
    }
  }
  function reportIntegrity() {
    try {
      parent.postMessage(
        {
          source: "octop-app",
          type: "violation",
          kind: "sandbox_integrity",
          directive: "sandbox-integrity",
          blockedURI: "",
          disposition: "enforce"
        },
        "*"
      );
    } catch (e) {}
  }
  function call(method, params, timeoutMs) {
    if (!intact()) {
      reportIntegrity();
      return Promise.reject(new Error("Sandbox integrity check failed"));
    }
    return new Promise(function (resolve, reject) {
      var id = ++seq;
      var entry = { resolve: resolve, reject: reject, timer: null };
      pending.set(id, entry);
      // The host may go away mid-request (the frame was remounted); reject
      // instead of leaving the caller's promise pending forever.
      entry.timer = setTimeout(function () {
        pending.delete(id);
        reject(new Error("Request timed out"));
      }, timeoutMs || TIMEOUT_MS);
      parent.postMessage(
        { source: "octop-app", type: "request", id: id, method: method, params: params || {} },
        "*"
      );
    });
  }
  function emitDataChange(detail) {
    for (var i = 0; i < dataListeners.length; i++) {
      try { dataListeners[i](detail); } catch (e) {}
    }
    try {
      window.dispatchEvent(new CustomEvent("octop:data-changed", { detail: detail }));
    } catch (e) {}
  }
  window.addEventListener("message", function (event) {
    var data = event.data;
    if (!data || data.source !== "octop-app-host") return;
    if (data.type === "data-changed") {
      emitDataChange(data.data || {});
      return;
    }
    var entry = pending.get(data.id);
    if (!entry) return;
    pending.delete(data.id);
    if (entry.timer) clearTimeout(entry.timer);
    if (data.ok) entry.resolve(data.data);
    else entry.reject(new Error(data.error || "Request failed"));
  });
  // Report what the browser blocked. A page that tries to reach a remote
  // origin after the fact is worth seeing in the audit log even though the
  // CSP already stopped it.
  window.addEventListener("securitypolicyviolation", function (event) {
    parent.postMessage(
      {
        source: "octop-app",
        type: "violation",
        directive: event.violatedDirective || "",
        blockedURI: event.blockedURI || "",
        disposition: event.disposition || ""
      },
      "*"
    );
  });
  window.octopApp = {
    me: function () { return call("me"); },
    schema: function (table) { return call("schema", { table: table || null }); },
    tables: function () { return call("tables"); },
    listRows: function (options) {
      var opts = options || {};
      return call("rows", {
        limit: opts.limit,
        offset: opts.offset,
        table: opts.table || null,
        filters: opts.filters,
        sort: opts.sort,
        expandRefs: opts.expandRefs === true
      });
    },
    aggregate: function (options) { return call("aggregate", options || {}); },
    appendRow: function (row, options) {
      return call("append", { row: row, table: (options || {}).table || null });
    },
    updateRow: function (rowId, values, options) {
      var opts = options || {};
      return call("update", {
        rowId: rowId || "",
        row: values || {},
        table: opts.table || null,
        expectedVersion: opts.expectedVersion
      });
    },
    updateRows: function (rows, options) {
      var opts = options || {};
      return call("updateMany", {
        rows: rows || [],
        table: opts.table || null
      });
    },
    deleteRow: function (rowId, options) {
      var opts = options || {};
      return call("remove", {
        rowId: rowId || "",
        table: opts.table || null,
        expectedVersion: opts.expectedVersion
      });
    },
    generate: function (prompt, options) {
      return call("generate", {
        prompt: prompt || "",
        system: (options || {}).system || ""
      });
    },
    upload: function (file) {
      if (!file || typeof file.arrayBuffer !== "function") {
        return Promise.reject(new Error("upload requires a File"));
      }
      return file.arrayBuffer().then(function (buffer) {
        return call("upload", {
          fileName: file.name || "upload.png",
          fileType: file.type || "",
          bytes: buffer
        }, 120000);
      });
    },
    runTask: function (options) {
      var opts = options || {};
      return call("runTask", {
        taskType: opts.taskType || "",
        table: opts.table || null,
        rowId: opts.rowId || "",
        input: opts.input || {},
        idempotencyKey: opts.idempotencyKey || ""
      });
    },
    getTask: function (taskId) {
      return call("getTask", { taskId: taskId || "" });
    },
    cancelTask: function (taskId) {
      return call("cancelTask", { taskId: taskId || "" });
    },
    launchGame: function (gameId) {
      return call("launchGame", { gameId: gameId || "" });
    },
    composeMessage: function (text) {
      return call("composeMessage", { text: text || "" });
    },
    onDataChange: function (callback) {
      if (typeof callback !== "function") return function () {};
      dataListeners.push(callback);
      return function () {
        var index = dataListeners.indexOf(callback);
        if (index >= 0) dataListeners.splice(index, 1);
      };
    },
    openExternal: function (url) { return call("openExternal", { url: url || "" }); },
    asset: function (name) { return call("asset", { name: name || "" }); },
    events: function (options) { return call("events", options || {}); },
    ackEvents: function (eventIds) {
      return call("ackEvents", { event_ids: eventIds || [] });
    },
    emitEvent: function (eventType, payload, options) {
      var opts = options || {};
      return call("emitEvent", {
        event_type: eventType || "",
        payload: payload || {},
        summary: opts.summary || "",
        client_event_id: opts.clientEventId || ""
      });
    },
    service: function (name) {
      return {
        call: function (tool, args) {
          return call("service", { name: name || "", tool: tool || "", args: args || {} });
        },
        callDetailed: function (tool, args) {
          return call("serviceDetailed", {
            name: name || "",
            tool: tool || "",
            args: args || {}
          });
        },
        image: function (url) {
          return call("serviceImage", { name: name || "", url: url || "" });
        },
        invoke: function (tool, args, options) {
          var opts = options || {};
          var key = opts.idempotencyKey || (
            "k_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
          );
          return call("serviceInvoke", {
            name: name || "",
            tool: tool || "",
            args: args || {},
            idempotencyKey: key,
            confirmationToken: opts.confirmationToken || ""
          });
        },
        operation: function (opId) {
          return call("serviceOperation", { name: name || "", opId: opId || "" });
        },
        resolveOperation: function (opId, status, options) {
          var opts = options || {};
          return call("serviceResolveOperation", {
            name: name || "",
            opId: opId || "",
            status: status || "failed",
            result: opts.result === undefined ? null : opts.result,
            errorMessage: opts.errorMessage || ""
          });
        },
        upload: function (file, options) {
          if (!file || typeof file.slice !== "function") {
            return Promise.reject(new Error("upload requires a File"));
          }
          var opts = options || {};
          return call("serviceUploadStart", {
            name: name || "",
            fileName: file.name || "upload",
            fileType: file.type || "",
            size: file.size,
            purpose: opts.purpose || ""
          }).then(function (start) {
            var uploadId = String((start && start.upload_id) || "");
            var chunkBytes = Number((start && start.max_chunk_bytes) || 4194304);
            var index = 0;
            function send(offset) {
              if (offset >= file.size) {
                return call("serviceUploadFinish", {
                  name: name || "",
                  uploadId: uploadId
                }, 300000);
              }
              var end = Math.min(offset + chunkBytes, file.size);
              return file.slice(offset, end).arrayBuffer().then(function (buffer) {
                return call("serviceUploadChunk", {
                  name: name || "",
                  uploadId: uploadId,
                  index: index,
                  bytes: buffer
                }, 120000).then(function () {
                  index += 1;
                  return send(end);
                });
              });
            }
            return send(0);
          });
        },
        mediaUrl: function (assetId) {
          return call("serviceMediaToken", {
            name: name || "",
            assetId: assetId || ""
          }).then(function (result) {
            return result && result.url ? result.url : "";
          });
        }
      };
    }
  };
})();`;

/**
 * Run before any app script.
 *
 * WebRTC is the one network channel CSP does not cover: an app that reads rows
 * through the bridge could otherwise open an `RTCPeerConnection` and send them
 * to a STUN/TURN server. The globals are replaced with non-configurable
 * `undefined` values on both the window and its prototype, so `delete` and
 * prototype lookups do not bring them back.
 *
 * A nested `srcdoc` frame would get a fresh, unfrozen realm (and `frame-src`
 * does not stop the initial srcdoc load), so this also rejects frame creation
 * and removes any frame that slips through. This is defence in depth: the
 * wrapper's `frame-src 'none'` and the write-time scan remain in place.
 */
export const WEBRTC_FREEZE = `(function () {
  var RTC = ["RTCPeerConnection", "webkitRTCPeerConnection", "mozRTCPeerConnection", "RTCDataChannel"];
  var EMBED = { IFRAME: 1, FRAME: 1, OBJECT: 1, EMBED: 1, PORTAL: 1 };
  var FRAME_MARKUP = /<\\s*(iframe|frame|object|embed|portal)\\b/i;

  function block(target, name) {
    try {
      Object.defineProperty(target, name, {
        value: undefined, configurable: false, writable: false
      });
    } catch (e) {}
  }
  function blockGlobal(name) {
    block(window, name);
    try {
      var proto = Object.getPrototypeOf(window);
      if (proto) block(proto, name);
    } catch (e) {}
  }
  for (var i = 0; i < RTC.length; i++) blockGlobal(RTC[i]);
  var constructors = [
    "HTMLIFrameElement", "HTMLFrameElement", "HTMLObjectElement", "HTMLEmbedElement",
    "Worker", "SharedWorker", "WebTransport", "open"
  ];
  for (var j = 0; j < constructors.length; j++) blockGlobal(constructors[j]);
  try {
    if (typeof Navigator !== "undefined") {
      block(Navigator.prototype, "serviceWorker");
      block(navigator, "serviceWorker");
    }
  } catch (e) {}

  function isEmbed(tag) {
    return Object.prototype.hasOwnProperty.call(EMBED, String(tag).toUpperCase());
  }
  function reject() {
    throw new Error("Frames are not available in a MiniApp");
  }
  function hasFrameMarkup(value) {
    return FRAME_MARKUP.test(String(value));
  }
  function containsEmbed(node) {
    if (!node || typeof node !== "object") return false;
    if (node.nodeType === 1 || node.nodeType === 11) {
      if (node.nodeType === 1 && isEmbed(node.tagName)) return true;
      if (node.querySelector && node.querySelector("iframe,frame,object,embed,portal")) {
        return true;
      }
    }
    return false;
  }
  function guardMethod(proto, name) {
    var original = proto[name];
    if (typeof original !== "function") return;
    Object.defineProperty(proto, name, {
      configurable: true,
      writable: true,
      value: function () {
        for (var i = 0; i < arguments.length; i++) {
          if (hasFrameMarkup(arguments[i]) || containsEmbed(arguments[i])) reject();
        }
        return original.apply(this, arguments);
      }
    });
  }
  function guardSetter(proto, name) {
    var desc = Object.getOwnPropertyDescriptor(proto, name);
    if (!desc || !desc.set) return;
    var original = desc.set;
    Object.defineProperty(proto, name, {
      configurable: true,
      get: desc.get,
      set: function (value) {
        if (hasFrameMarkup(value)) reject();
        return original.call(this, value);
      }
    });
  }

  var createElement = Document.prototype.createElement;
  Document.prototype.createElement = function (tag, options) {
    if (isEmbed(tag)) reject();
    return createElement.call(this, tag, options);
  };
  var createElementNS = Document.prototype.createElementNS;
  Document.prototype.createElementNS = function (ns, tag, options) {
    if (isEmbed(tag)) reject();
    return createElementNS.call(this, ns, tag, options);
  };

  guardSetter(Element.prototype, "innerHTML");
  guardSetter(Element.prototype, "outerHTML");
  if (typeof ShadowRoot !== "undefined") guardSetter(ShadowRoot.prototype, "innerHTML");
  guardMethod(Element.prototype, "insertAdjacentHTML");
  guardMethod(Element.prototype, "append");
  guardMethod(Element.prototype, "prepend");
  guardMethod(Element.prototype, "replaceChildren");
  guardMethod(Element.prototype, "insertAdjacentElement");
  guardMethod(Element.prototype, "setHTMLUnsafe");
  guardMethod(Element.prototype, "setHTML");
  if (typeof ShadowRoot !== "undefined") {
    guardMethod(ShadowRoot.prototype, "setHTMLUnsafe");
    guardMethod(ShadowRoot.prototype, "setHTML");
  }
  guardMethod(Document.prototype, "write");
  guardMethod(Document.prototype, "writeln");
  guardMethod(Document.prototype, "execCommand");
  guardMethod(Document.prototype, "parseHTMLUnsafe");
  guardMethod(Document, "parseHTMLUnsafe");
  if (typeof DOMParser !== "undefined") guardMethod(DOMParser.prototype, "parseFromString");
  if (typeof Range !== "undefined") guardMethod(Range.prototype, "createContextualFragment");

  function removeEmbeds(root) {
    if (!root || root.nodeType !== 1) return;
    if (isEmbed(root.tagName)) {
      try { root.remove(); } catch (e) {}
      return;
    }
    if (root.querySelectorAll) {
      var nodes = root.querySelectorAll("iframe,frame,object,embed,portal");
      for (var k = nodes.length - 1; k >= 0; k--) {
        try { nodes[k].remove(); } catch (e) {}
      }
    }
  }
  try {
    var observer = new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) removeEmbeds(added[j]);
      }
    });
    observer.observe(document, { childList: true, subtree: true });
  } catch (e) {}
  document.addEventListener("DOMContentLoaded", function () {
    removeEmbeds(document.documentElement);
  });
})();`;

/** Whether the app document embeds a frame/plugin element. */
export function hasEmbeddedFrame(html: string): boolean {
  return /<\s*(iframe|frame|object|embed|portal)\b/i.test(html);
}

/**
 * Host-level presentation reset for every MiniApp document.
 *
 * Generated apps often theme `body` but leave the root scroll container and
 * native scrollbars unthemed. Inside a dark desktop window that shows up as a
 * white strip on the right/bottom edge and white rubber-band gaps while
 * scrolling. Keep the scrollbars transparent on the app background, and stop
 * overscroll from revealing the iframe's default white canvas.
 */
const APP_HOST_STYLE = `<style>
  html, body { overscroll-behavior: none; }
  html {
    background: var(--bg, #fff);
    scrollbar-color: rgb(148 163 184 / 55%) var(--bg, transparent);
  }
  ::-webkit-scrollbar { width: 8px; height: 8px; }
  ::-webkit-scrollbar-track { background: var(--bg, transparent); }
  ::-webkit-scrollbar-corner { background: var(--bg, #fff); }
  ::-webkit-scrollbar-thumb {
    background: rgb(148 163 184 / 55%);
    border-radius: 999px;
  }
  ::-webkit-scrollbar-thumb:hover { background: rgb(148 163 184 / 70%); }
</style>`;

export interface BuildSrcDocOptions {
  /** Bridge SDK injected before app scripts. Defaults to ``window.octopApp``. */
  sdk?: string;
  /** Locale exposed to system apps as ``window.__OCTOP_LOCALE__``. */
  locale?: string;
  /** CSP for this app; defaults to the static APP_CSP. */
  csp?: string;
}

export function buildSrcDoc(
  html: string,
  options: BuildSrcDocOptions = {},
): string {
  // Old apps may predate the write-time rule; never run one whose static HTML
  // would create a fresh realm before the freeze can protect it.
  if (hasEmbeddedFrame(html)) {
    return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${
      options.csp ?? APP_CSP
    }">${APP_HOST_STYLE}</head><body><p>This MiniApp embeds a frame and cannot be displayed.</p></body></html>`;
  }
  const localeScript = options.locale
    ? `<script>window.__OCTOP_LOCALE__=${JSON.stringify(
        options.locale,
      )};</script>`
    : "";
  const head = `<meta http-equiv="Content-Security-Policy" content="${
    options.csp ?? APP_CSP
  }">${localeScript}<script>${
    options.sdk ?? APP_SDK
  }</script>${APP_HOST_STYLE}`;
  // Pull the first <html ...> tag out so the freeze script can precede every
  // app script while keeping lang/class/style on the root element.
  const htmlMatch = /<html\b([^>]*)>/i.exec(html);
  const htmlAttrs = htmlMatch ? htmlMatch[1] : "";
  let body = html;
  if (htmlMatch?.index !== undefined) {
    body =
      html.slice(0, htmlMatch.index) +
      html.slice(htmlMatch.index + htmlMatch[0].length);
  }
  body = body.replace(/^\s*<!doctype[^>]*>/i, "");
  const headMatch = /<head[^>]*>/i.exec(body);
  if (headMatch?.index !== undefined) {
    const at = headMatch.index + headMatch[0].length;
    body = `${body.slice(0, at)}${head}${body.slice(at)}`;
  } else {
    const bodyMatch = /<body[^>]*>/i.exec(body);
    if (bodyMatch?.index !== undefined) {
      body = `${body.slice(0, bodyMatch.index)}<head>${head}</head>${body.slice(
        bodyMatch.index,
      )}`;
    } else {
      body = `<head>${head}</head><body>${body}</body>`;
    }
  }
  return `<!doctype html><html${htmlAttrs}><script>${WEBRTC_FREEZE}</script>${body}`;
}
