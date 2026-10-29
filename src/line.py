"""LINE Official Account (Messaging API): webhook in, the same question path as /chat, Reply API out.

POST /line/webhook is registered only when LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN are both
set. Every request is signature-checked on the raw body before anything is parsed, acknowledged with
200 at once, and handled in a background task, well inside the reply token's validity (~1 minute).

Reply only, never push: LineClient can call the Reply API and the loading animation, nothing else.
Push messages count against the free plan's monthly quota, so a reply token that has expired is
logged and dropped. Only 1:1 chats are answered.

Privacy: the LINE userId is used to call the loading animation and as the per-user rate-limit key
(salted HMAC in src/limits.py); it and the reply token are never logged or stored. Questions are
logged by the shared question path exactly like /chat (redacted).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from src.config import AppSettings
from src.limits import RateLimiter
from src.line_signature import verify_signature

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"
API_TIMEOUT_S = 10.0
MAX_BODY_BYTES = 256 * 1024
# LINE guarantees a reply token for about a minute; past this age we don't spend an LLM call on it.
REPLY_DEADLINE_S = 50
LOADING_SECONDS = 20  # LINE accepts 5–60 in steps of 5; the next message clears it early
MAX_TEXT_CHARS = 5000  # Messaging API limit for one text message
QUICK_REPLY_LABEL_CHARS = 20  # Messaging API limit for a quick-reply button label
ASK_POSTBACK = "action=ask"  # the rich menu's 質問する button (scripts/line_rich_menu.py)

# Same wording as src.app.RATE_LIMITED_DETAIL (a test keeps them equal).
RATE_LIMITED_REPLY = "ただいまご質問が集中しております。恐れ入りますが、しばらく時間をおいてから再度お試しください。"
# The web chat's version also offers LINE, which is where this customer already is.
BUDGET_REPLY = ("申し訳ございません。ただいま自動応答をご利用いただけません。"
                "お手数ですが、お電話にて当院へ直接お問い合わせください。")
NON_TEXT_REPLY = "申し訳ございません。スタンプや画像にはお答えできません。ご質問を文字でお送りください。"
ASK_REPLY = "ご質問をメッセージでお送りください。下のボタンからもお選びいただけます。"


def greeting(clinic_name: str) -> str:
    return (f"{clinic_name}（デモ）を友だち追加いただき、ありがとうございます。\n\n"
            "このアカウントはAI受付のデモです。院は架空のもので、ご予約やスタッフからの返信はできません。\n"
            "料金・営業時間・アクセスなど、院の資料に書かれていることにAIがお答えします。\n\n"
            "お名前・電話番号などの個人情報は送らないでください。\n"
            "下のボタンやメニューからお試しください。")


def too_long_reply(max_chars: int) -> str:
    return f"恐れ入りますが、ご質問は{max_chars}文字以内でお送りください。"


class LineClient:
    """The only LINE API calls the app makes. Each returns "ok", "token_invalid" or "error:<status>"."""

    def __init__(self, access_token: str, http: httpx.Client | None = None) -> None:
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._http = http or httpx.Client(timeout=API_TIMEOUT_S)

    def reply(self, reply_token: str, messages: list[dict[str, Any]]) -> str:
        return self._post(REPLY_URL, {"replyToken": reply_token, "messages": messages})

    def show_loading(self, user_id: str, seconds: int) -> str:
        return self._post(LOADING_URL, {"chatId": user_id, "loadingSeconds": seconds})

    def _post(self, url: str, payload: dict[str, Any]) -> str:
        try:
            res = self._http.post(url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            return f"error:{type(exc).__name__}"
        if res.status_code == 200:
            return "ok"
        try:  # read LINE's error only to classify it; its body is never logged
            message = str(res.json().get("message", ""))
        except ValueError:
            message = ""
        if res.status_code == 400 and "reply token" in message.lower():
            return "token_invalid"
        return f"error:{res.status_code}"


_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# [ \t], not \s: \s would also match the newline before a line and swallow blank lines.
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.MULTILINE)
_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)


def format_for_line(answer: str, behavior: str, sources: list[str], doc_labels: dict[str, str]) -> str:
    """Plain text for a LINE bubble: markdown removed, plus the cited docs' short labels for answers."""
    text = _LINK.sub(r"\1", answer)
    text = text.replace("**", "").replace("__", "")
    text = _HEADING.sub("", text)
    text = _BULLET.sub("・", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if behavior == "answer" and sources:
        labels = "／".join(doc_labels.get(s, s) for s in dict.fromkeys(sources))
        text += f"\n\n（参照：{labels}）"
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS - 1] + "…"
    return text


def text_message(text: str, suggestions: list[str]) -> dict[str, Any]:
    """A text message with the quick-reply buttons (each sends its question as the customer)."""
    message: dict[str, Any] = {"type": "text", "text": text}
    if suggestions:
        message["quickReply"] = {"items": [
            {"type": "action", "action": {"type": "message", "label": q[:QUICK_REPLY_LABEL_CHARS], "text": q}}
            for q in suggestions]}
    return message


def register_line_routes(
    app: FastAPI,
    settings: AppSettings,
    ask: Callable[..., Any],
    log_event: Callable[..., None],
    site: dict[str, Any],
    client: LineClient | None = None,
    limiter: RateLimiter | None = None,
    clock: Callable[[], float] = time.time,
) -> bool:
    """Add POST /line/webhook if both LINE secrets are set. Returns whether it was added."""
    if not (settings.line_channel_secret and settings.line_channel_access_token):
        return False
    client = client or LineClient(settings.line_channel_access_token)
    # Same numbers as the per-IP limits, in its own namespace, keyed by the LINE userId.
    limiter = limiter or RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_per_day)
    tenant = settings.allowed_tenants[0]  # one channel = one clinic
    doc_labels = site.get("doc_labels", {})
    suggestions = list(site.get("suggestions", {}).get("line", []))
    hello = greeting(site.get("clinic_name", tenant))

    def log(**fields: Any) -> None:
        log_event(event="line", **fields)

    def expired(event: dict[str, Any]) -> bool:
        sent_ms = event.get("timestamp")
        return isinstance(sent_ms, int) and clock() - sent_ms / 1000 > REPLY_DEADLINE_S

    def reply(event: dict[str, Any], text: str, event_type: str, **fields: Any) -> None:
        if expired(event):
            log(event_type=event_type, reply="expired", **fields)
            return
        result = client.reply(event.get("replyToken", ""), [text_message(text, suggestions)])
        log(event_type=event_type, reply=result, **fields)  # token_invalid: dropped, never pushed

    def handle_text(event: dict[str, Any], user_id: str, text: str) -> None:
        if expired(event):  # before the LLM call: a reply could no longer be delivered
            log(event_type="text", reply="expired")
            return
        if not text or len(text) > settings.max_message_chars:
            reply(event, too_long_reply(settings.max_message_chars), "text", outcome="invalid")
            return
        allowed, _ = limiter.check(user_id)
        if not allowed:
            reply(event, RATE_LIMITED_REPLY, "text", outcome="rate_limited")
            return
        client.show_loading(user_id, LOADING_SECONDS)  # best effort; the reply clears it
        res = ask(tenant, text, "line", budget_answer=BUDGET_REPLY)
        reply(event, format_for_line(res.answer, res.behavior, res.sources, doc_labels), "text",
              behavior=res.behavior)

    def handle_event(event: dict[str, Any]) -> None:
        kind = event.get("type")
        source = event.get("source") or {}
        user_id = source.get("userId")
        if source.get("type") != "user" or not user_id:
            log(event_type=kind, outcome="ignored", reason="non_user_source")
            return
        if kind == "follow":
            reply(event, hello, "follow")
        elif kind == "postback" and (event.get("postback") or {}).get("data") == ASK_POSTBACK:
            reply(event, ASK_REPLY, "postback")
        elif kind == "message":
            message = event.get("message") or {}
            if message.get("type") == "text":
                handle_text(event, user_id, str(message.get("text", "")).strip())
            else:
                reply(event, NON_TEXT_REPLY, message.get("type", "unknown"))
        # unfollow, unsend, other postbacks, ...: nothing to answer

    def handle_events(events: list[Any]) -> None:
        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                handle_event(event)
            except Exception as exc:  # one bad event must not stop the rest
                log(event_type=event.get("type"), outcome="error", error=type(exc).__name__)

    @app.post("/line/webhook", include_in_schema=False)
    async def line_webhook(request: Request, background: BackgroundTasks) -> dict[str, Any]:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise HTTPException(413)
        body = await request.body()  # raw bytes: the signature covers exactly these
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413)
        signature = request.headers.get("x-line-signature")
        if not signature:
            log(outcome="bad_signature", reason="missing")
            raise HTTPException(400, detail="missing signature")
        if not verify_signature(settings.line_channel_secret, body, signature):
            log(outcome="bad_signature", reason="invalid")
            raise HTTPException(401, detail="invalid signature")
        try:
            events = json.loads(body).get("events", [])
        except (ValueError, AttributeError):
            raise HTTPException(400, detail="invalid body")
        if isinstance(events, list) and events:
            background.add_task(handle_events, events)  # runs after the 200 has been sent
        return {}

    return True
