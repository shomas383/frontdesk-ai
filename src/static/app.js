// Web chat for the AI front desk. No storage: the conversation lives only in this page's memory.
"use strict";

const SUGGESTIONS = [
  "60分の料金は？",
  "12月29日は何時まで？",
  "ブログに6,600円って書いてありました",
  "ギフト券はありますか？",
  "腰痛は何回で治りますか？",
];
const BEHAVIOR_TAGS = {
  handoff: "スタッフにおつなぎします",
  refuse_redirect: "医療機関へのご相談をおすすめ",
};
const WELCOME =
  "こんにちは。こもれび整体院のAIフロントデスクです。料金・営業時間・ご予約などについて、院の資料をもとにお答えします。";
const MSG_NETWORK = "接続できませんでした。通信環境をご確認のうえ、もう一度お試しください。";
const MSG_SERVER = "ただいま応答できません。お手数ですが、少し時間をおいてお試しください。";
const MSG_TOO_LONG = (n) => `ご質問は${n}文字以内でご入力ください。`;
const MSG_RATE = (s) => `ご質問が続いたため、少し時間をおいてからお試しください（あと${s}秒）。`;

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const input = $("message");
const counter = $("counter");
const sendButton = $("send");
const form = $("composer");
const suggestions = $("suggestions");
const chips = $("chips");
const banner = $("banner");
const composer = $("composer");
const desktop = window.matchMedia("(min-width: 720px)");
// /embed: the compact chat inside widget.js's panel on a clinic's site; the tenant comes from the URL.
const embedded = document.body.classList.contains("embed");

const state = {
  tenant: null,
  labels: {},
  maxChars: 300,
  busy: false,
  cooldownUntil: 0,
  cooldownTimer: null,
  cooldownNotice: null,
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // text only, never parsed as HTML: answers are model output
  return node;
}

function scrollToBottom() {
  chat.scrollTop = chat.scrollHeight;
}

function addUserMessage(text) {
  const msg = el("div", "msg user");
  msg.append(el("div", "bubble", text));
  chat.append(msg);
  scrollToBottom();
}

function addBotMessage(text, behavior, sources) {
  const msg = el("div", "msg bot");
  msg.append(el("div", "bubble", text));
  const meta = el("div", "meta");
  if (BEHAVIOR_TAGS[behavior]) meta.append(el("span", `tag ${behavior}`, BEHAVIOR_TAGS[behavior]));
  for (const docId of sources || []) {
    const chip = el("span", "source", state.labels[docId] || docId);
    chip.title = `出典: ${docId}`;
    meta.append(chip);
  }
  if (meta.childElementCount) msg.append(meta);
  chat.append(msg);
  scrollToBottom();
}

function addNotice(text) {
  const notice = el("div", "notice", text);
  notice.setAttribute("role", "status");
  chat.append(notice);
  scrollToBottom();
  return notice;
}

function showTyping() {
  const msg = el("div", "msg bot");
  const bubble = el("div", "bubble typing");
  bubble.setAttribute("aria-label", "回答を作成中");
  bubble.append(el("span"), el("span"), el("span"));
  msg.append(bubble);
  chat.append(msg);
  scrollToBottom();
  return msg;
}

function coolingDown() {
  return Date.now() < state.cooldownUntil;
}

function updateComposer() {
  const length = input.value.trim().length;
  const raw = input.value.length;
  counter.textContent = `${raw}/${state.maxChars}`;
  counter.classList.toggle("over", raw > state.maxChars);
  sendButton.disabled = state.busy || coolingDown() || length === 0 || length > state.maxChars;
  for (const chip of chips.children) chip.disabled = state.busy || coolingDown();
}

function autoGrow() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
}

function startCooldown(seconds) {
  state.cooldownUntil = Date.now() + seconds * 1000;
  clearInterval(state.cooldownTimer);
  state.cooldownNotice = addNotice(MSG_RATE(seconds));
  state.cooldownTimer = setInterval(() => {
    const left = Math.ceil((state.cooldownUntil - Date.now()) / 1000);
    if (left <= 0) {
      clearInterval(state.cooldownTimer);
      state.cooldownNotice.textContent = "もう一度ご質問いただけます。";
    } else {
      state.cooldownNotice.textContent = MSG_RATE(left);
    }
    updateComposer();
  }, 1000);
  updateComposer();
}

async function ask(text) {
  const message = text.trim();
  if (!message || state.busy || coolingDown()) return;
  if (message.length > state.maxChars) {
    addNotice(MSG_TOO_LONG(state.maxChars));
    return;
  }
  suggestions.hidden = true;
  banner.classList.add("compact");
  addUserMessage(message);
  state.busy = true;
  updateComposer();
  const typing = showTyping();
  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tenant: state.tenant, message }),
    });
    const body = await response.json().catch(() => ({}));
    typing.remove();
    if (response.ok) {
      addBotMessage(body.answer, body.behavior, body.sources);
    } else if (response.status === 429) {
      startCooldown(Math.max(1, parseInt(response.headers.get("Retry-After"), 10) || 30));
    } else if (response.status === 422) {
      addNotice(typeof body.detail === "string" ? body.detail : MSG_TOO_LONG(state.maxChars));
    } else {
      addNotice(MSG_SERVER);
    }
  } catch {
    typing.remove();
    addNotice(MSG_NETWORK);
  } finally {
    state.busy = false;
    updateComposer();
  }
}

// Desktop: chips sit right under the welcome message. Phones: just above the composer, near the thumb.
function placeSuggestions() {
  if (desktop.matches) {
    const welcome = chat.querySelector(".msg.bot");
    if (welcome) welcome.after(suggestions);
  } else {
    composer.before(suggestions);
  }
}

function renderSuggestions() {
  for (const question of SUGGESTIONS) {
    const chip = el("button", "chip", question);
    chip.type = "button";
    chip.addEventListener("click", () => ask(question));
    chips.append(chip);
  }
}

// ?demo=q1|q2 auto-sends questions (screenshots, demo video). Only when the server sets DEMO_AUTOSEND.
async function runDemo() {
  const param = new URLSearchParams(location.search).get("demo");
  if (!param) return;
  for (const question of param.split("|").map((q) => q.trim()).filter(Boolean)) {
    await ask(question);
  }
}

async function init() {
  renderSuggestions();
  addBotMessage(WELCOME);
  placeSuggestions();
  desktop.addEventListener("change", placeSuggestions);
  let config = {};
  const tenant = new URLSearchParams(location.search).get("tenant") || "";
  try {
    const response = await fetch(embedded ? `/api/config?tenant=${encodeURIComponent(tenant)}` : "/api/config");
    if (response.ok) config = await response.json();
  } catch {
    addNotice(MSG_NETWORK);
  }
  state.tenant = config.tenant || null;
  state.labels = config.doc_labels || {};
  state.maxChars = config.max_message_chars || 300;
  if (config.clinic_name) $("clinic-name").textContent = config.clinic_name;
  updateComposer();
  if (config.demo_autosend) runDemo();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value;
  if (sendButton.disabled) return;
  input.value = "";
  autoGrow();
  ask(text);
});

input.addEventListener("input", () => {
  autoGrow();
  updateComposer();
});

// Enter sends on devices with a keyboard; on touch devices Enter is a newline and the button sends.
// Never send while an IME is composing (Japanese input confirms conversions with Enter).
const touchOnly = window.matchMedia("(pointer: coarse)").matches;
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229 && !touchOnly) {
    event.preventDefault();
    form.requestSubmit();
  }
});

// Embedded: Esc asks widget.js to close the panel, and the panel asks us for focus when it opens.
// The messages carry no conversation data, so posting to any parent origin ("*") reveals nothing.
if (embedded) {
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) parent.postMessage({ type: "frontdesk:close" }, "*");
  });
  window.addEventListener("message", (event) => {
    // Not on touch devices: focusing the textarea would pop up the keyboard over the answer.
    if (event.source === parent && event.data && event.data.type === "frontdesk:focus" && !touchOnly) input.focus();
  });
}

init();
