// Website chat widget. A clinic adds one tag to its own site:
//   <script src="<our origin>/widget.js" data-tenant="<tenant>" [data-offset-bottom="64"] async></script>
// A launcher button opens a panel with our chat (/embed) in an iframe, isolated from the site. No
// dependencies, no requests of its own, nothing stored; the iframe is created on first open.
// API for the site's own buttons: window.FrontdeskWidget.open() / .close() / .toggle()
"use strict";
(() => {
  if (window.FrontdeskWidget) return; // the tag was added twice
  const script = document.currentScript;
  const tenant = script && script.dataset.tenant;
  if (!tenant) return console.warn("FrontdeskWidget: data-tenant is missing");
  const api = (window.FrontdeskWidget = {});
  const origin = new URL(script.src).origin;
  // Room for a site's sticky bottom bar (e.g. 電話 / LINE予約), so the launcher doesn't cover it.
  const offset = Math.min(300, Math.max(0, parseInt(script.dataset.offsetBottom, 10) || 0));
  const NS = "http://www.w3.org/2000/svg";

  const CSS = `
:host { all: initial; }
* { box-sizing: border-box; }
.launcher, .panel { position: fixed; right: 16px; z-index: 2147483000; }
.launcher {
  bottom: calc(${offset + 16}px + env(safe-area-inset-bottom)); width: 56px; height: 56px; border: 0;
  border-radius: 50%; background: #3f7a57; color: #fff; display: grid; place-items: center;
  cursor: pointer; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.25);
}
.launcher[aria-expanded="true"] .i-chat, .launcher[aria-expanded="false"] .i-close { display: none; }
button:focus-visible { outline: 3px solid #f3c969; outline-offset: 2px; }
.panel {
  bottom: calc(${offset + 84}px + env(safe-area-inset-bottom)); width: 380px; height: 600px;
  max-width: calc(100vw - 32px); max-height: calc(100vh - ${offset + 100}px);
  display: flex; flex-direction: column; overflow: hidden; border-radius: 16px; background: #f5f3ec;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
}
.panel[hidden] { display: none; }
.bar {
  display: flex; align-items: center; justify-content: space-between; flex: none;
  padding: 4px 4px 4px 14px; background: #3f7a57; color: #fff;
  font: 600 14px/1.4 "Hiragino Sans", "Yu Gothic UI", Meiryo, system-ui, sans-serif;
}
.bar b { margin-right: 6px; }
.bar b:empty { display: none; }
.close {
  width: 40px; height: 40px; border: 0; border-radius: 50%; background: transparent; color: #fff;
  display: grid; place-items: center; cursor: pointer;
}
iframe { flex: 1; width: 100%; border: 0; background: #f5f3ec; }
svg { width: 24px; height: 24px; }
.i-chat path { fill: currentColor; }
.i-close path { fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; }
/* Phones: the panel takes the whole screen, and the launcher hides behind it. */
@media (max-width: 480px) {
  .panel { inset: 0; width: auto; height: auto; max-width: none; max-height: none; border-radius: 0; }
  .bar { padding-top: max(4px, env(safe-area-inset-top)); }
  .open .launcher { display: none; }
}`;

  function el(tag, className, attrs) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
    return node;
  }

  function icon(name, d) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", name);
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
    return svg;
  }
  const CHAT = "M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H10l-5 4v-4H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z";
  const CROSS = "M6 6l12 12M18 6 6 18";

  function init() {
    const host = el("div");
    const root = host.attachShadow({ mode: "open" }); // the site's CSS can't reach in (open: testable)
    // A constructed stylesheet isn't inline CSS, so a strict CSP on the site (no 'unsafe-inline') allows it.
    if ("adoptedStyleSheets" in root) {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      root.adoptedStyleSheets = [sheet];
    } else {
      root.append(Object.assign(el("style"), { textContent: CSS }));
    }

    const wrap = el("div");
    const launcher = el("button", "launcher", {
      type: "button", "aria-label": "AI受付に質問する", "aria-expanded": "false", "aria-controls": "panel",
    });
    launcher.append(icon("i-chat", CHAT), icon("i-close", CROSS));
    const panel = el("div", "panel", { id: "panel", role: "dialog", "aria-label": "AI受付" });
    panel.hidden = true;
    const bar = el("div", "bar");
    const close = el("button", "close", { type: "button", "aria-label": "チャットを閉じる" });
    close.append(icon("i-close", CROSS));
    // The close button lives outside the iframe, so the panel closes even if the chat can't load.
    const title = el("span");
    const name = el("b");
    title.append(name, "AI受付");
    bar.append(title, close);
    panel.append(bar);
    wrap.append(panel, launcher);
    root.append(wrap);
    document.body.append(host);

    let frame = null;
    let opener = null;

    function focusChat() {
      if (panel.hidden || !frame) return;
      frame.focus();
      // Dropped by the browser unless the frame really shows our page (not a refused-frame error page).
      frame.contentWindow.postMessage({ type: "frontdesk:focus" }, origin);
    }

    function openPanel() {
      if (!panel.hidden) return;
      const active = document.activeElement;
      opener = active && active !== document.body && active !== host ? active : launcher;
      if (!frame) {
        frame = el("iframe", "", {
          title: "AI受付", src: `${origin}/embed?tenant=${encodeURIComponent(tenant)}`, referrerpolicy: "no-referrer",
        });
        frame.addEventListener("load", focusChat);
        panel.append(frame);
      }
      panel.hidden = false;
      wrap.className = "open";
      launcher.setAttribute("aria-expanded", "true");
      focusChat();
    }

    function closePanel() {
      if (panel.hidden) return;
      panel.hidden = true;
      wrap.className = "";
      launcher.setAttribute("aria-expanded", "false");
      (opener && opener.isConnected ? opener : launcher).focus();
    }

    launcher.addEventListener("click", () => (panel.hidden ? openPanel() : closePanel()));
    close.addEventListener("click", closePanel);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closePanel();
    });
    // From /embed, and only from our own frame: its clinic name for the bar, and Esc pressed inside it.
    window.addEventListener("message", (event) => {
      const data = event.data;
      if (!frame || event.source !== frame.contentWindow || event.origin !== origin || !data) return;
      if (data.type === "frontdesk:close") closePanel();
      if (data.type === "frontdesk:ready" && typeof data.clinicName === "string") {
        name.textContent = data.clinicName.slice(0, 40); // text only, never HTML
        panel.setAttribute("aria-label", `${name.textContent} AI受付`);
      }
    });

    Object.assign(api, {
      open: openPanel,
      close: closePanel,
      toggle: () => (panel.hidden ? openPanel() : closePanel()),
    });
  }

  if (document.body) init();
  else document.addEventListener("DOMContentLoaded", init);
})();
