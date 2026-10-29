import base64
import hashlib
import hmac
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import src.app
from src.answer import answer_question
from src.config import AppSettings, ROOT_DIR
from src.limits import RateLimiter
from src.line import (ASK_REPLY, BUDGET_REPLY, LOADING_URL, NON_TEXT_REPLY, QUICK_REPLY_LABEL_CHARS,
                      RATE_LIMITED_REPLY, REPLY_URL, LineClient, format_for_line)
from tests.test_app import Clock, FakeProvider, FakeUsage, fake_connect, retrieved  # noqa: F401

SECRET = "line-test-secret"
TOKEN = "line-test-access-token"
USER = "U0123456789abcdef0123456789abcdef"
OTHER_USER = "Ufedcba9876543210fedcba9876543210"
NOW = 1_790_000_000.0  # seconds; LINE timestamps are milliseconds


class FakeLine:
    """Records what the app asks LINE to do. Only the two methods LineClient has."""

    def __init__(self, reply_result="ok"):
        self.calls = []
        self.reply_result = reply_result

    def reply(self, reply_token, messages):
        self.calls.append(("reply", reply_token, messages))
        return self.reply_result

    def show_loading(self, user_id, seconds):
        self.calls.append(("loading", user_id, seconds))
        return "ok"

    def replies(self):
        return [c for c in self.calls if c[0] == "reply"]


def sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def make_line_client(provider=None, usage=None, line=None, clock=None, now=NOW, secrets=True, **overrides):
    settings = AppSettings(**{"app_today": "2026-10-24",
                              **({"line_channel_secret": SECRET, "line_channel_access_token": TOKEN}
                                 if secrets else {}), **overrides})
    clock = clock or Clock()
    limiter = RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_per_day, clock, lambda: clock.wall)
    line_limiter = RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_per_day, clock,
                               lambda: clock.wall)
    app = src.app.create_app(settings, provider or FakeProvider(), fake_connect, usage or FakeUsage(), limiter,
                             warm=False, line_client=line if line is not None else FakeLine(),
                             line_limiter=line_limiter, line_clock=lambda: now)
    return TestClient(app), line_limiter


def event(kind="message", user=USER, text="60分の全身整体はいくらですか？", token="rt-1", sent=NOW, **extra):
    e = {"type": kind, "timestamp": int(sent * 1000), "replyToken": token, "mode": "active",
         "webhookEventId": "01H000", "source": {"type": "user", "userId": user}}
    if kind == "message":
        e["message"] = {"type": "text", "id": "1", "text": text}
    e.update(extra)
    return e


def post(client, *events, signature=None, body=None):
    body = body if body is not None else json.dumps(
        {"destination": "Uxxxxxxxx", "events": list(events)}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signature is not False:
        headers["x-line-signature"] = sign(body) if signature is None else signature
    return client.post("/line/webhook", content=body, headers=headers)


def _line_logs(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records
            if r.name == "frontdesk.requests" and '"event": "line"' in r.getMessage()]


# --- route and signature ----------------------------------------------------------------------------

def test_route_is_off_without_secrets(caplog):
    line = FakeLine()
    with caplog.at_level(logging.INFO):
        client, _ = make_line_client(line=line, secrets=False)
        with client:
            assert post(client, event()).status_code == 404
    startup = [json.loads(r.getMessage()) for r in caplog.records if '"event": "startup"' in r.getMessage()]
    assert startup[-1]["line_enabled"] is False
    assert line.calls == []


@pytest.mark.parametrize("missing", ["line_channel_secret", "line_channel_access_token"])
def test_route_is_off_with_one_secret_missing(missing):
    client, _ = make_line_client(**{missing: ""})
    with client:
        assert post(client, event()).status_code == 404


def test_startup_reports_line_enabled(caplog):
    with caplog.at_level(logging.INFO):
        client, _ = make_line_client()
        with client:
            pass
    startup = [json.loads(r.getMessage()) for r in caplog.records if '"event": "startup"' in r.getMessage()]
    assert startup[-1]["line_enabled"] is True


@pytest.mark.parametrize("signature, status", [(False, 400), ("", 400), ("bm90IHRoZSBzaWduYXR1cmU=", 401)])
def test_bad_signature_is_rejected_before_anything_runs(retrieved, signature, status):
    provider, line = FakeProvider(), FakeLine()
    client, _ = make_line_client(provider, line=line)
    with client:
        assert post(client, event(), signature=signature).status_code == status
    assert line.calls == [] and provider.calls == 0


def test_signature_over_a_different_body_is_rejected(retrieved):
    line = FakeLine()
    client, _ = make_line_client(line=line)
    original = json.dumps({"events": [event()]}).encode()
    tampered = original.replace(b"rt-1", b"rt-2")
    with client:
        assert post(client, body=tampered, signature=sign(original)).status_code == 401
    assert line.calls == []


def test_signature_is_checked_on_the_raw_bytes(retrieved):
    # CRLF and non-ASCII text exactly as sent; the app must not re-encode before verifying.
    line = FakeLine()
    client, _ = make_line_client(line=line)
    body = json.dumps({"events": [event(text="😊料金は？")]}, ensure_ascii=False, indent=1).replace(
        "\n", "\r\n").encode("utf-8")
    with client:
        assert post(client, body=body).status_code == 200
    assert len(line.replies()) == 1


def test_verify_request_with_no_events():
    line = FakeLine()
    client, _ = make_line_client(line=line)
    with client:
        res = post(client)
    assert res.status_code == 200 and res.json() == {}
    assert line.calls == []


def test_oversized_body_is_rejected():
    client, _ = make_line_client()
    with client:
        body = b'{"events": []}' + b" " * (256 * 1024)
        assert post(client, body=body).status_code == 413


def test_malformed_json_with_a_valid_signature():
    client, _ = make_line_client()
    with client:
        assert post(client, body=b"not json").status_code == 400


# --- answering ---------------------------------------------------------------------------------------

def test_text_message_gets_one_reply_with_its_token(retrieved):
    line = FakeLine()
    client, _ = make_line_client(line=line)
    with client:
        assert post(client, event(token="rt-abc")).status_code == 200
    (_, token, messages), = line.replies()
    assert token == "rt-abc"
    assert len(messages) == 1 and messages[0]["type"] == "text"
    assert messages[0]["text"] == "60分の全身整体は6,600円（税込）です。\n\n（参照：料金・メニュー）"
    items = messages[0]["quickReply"]["items"]
    assert [i["action"]["text"] for i in items] == ["60分の料金は？", "営業時間は？", "駐車場はありますか？",
                                                    "初めてでも大丈夫ですか？"]


def test_loading_animation_goes_out_before_the_reply(retrieved):
    line = FakeLine()
    client, _ = make_line_client(line=line)
    with client:
        post(client, event())
    assert [c[0] for c in line.calls] == ["loading", "reply"]
    assert line.calls[0][1] == USER and line.calls[0][2] % 5 == 0 and 5 <= line.calls[0][2] <= 60


def test_line_uses_the_eval_defaults(retrieved, monkeypatch):
    calls = []

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return answer_question(*args, **kwargs)

    monkeypatch.setattr(src.app, "answer_question", spy)
    client, _ = make_line_client()
    with client:
        post(client, event())
    assert calls == [{}]


def test_guardrail_and_behaviors_come_through(retrieved):
    provider = FakeProvider({"answer": "腰痛の原因は判断できかねます。医療機関へのご相談をおすすめします。",
                             "behavior": "refuse_redirect", "sources": ["06_faq"]})
    line = FakeLine()
    client, _ = make_line_client(provider, line=line)
    with client:
        post(client, event(text="腰痛は治りますか？"))
    text = line.replies()[0][2][0]["text"]
    assert "医療機関" in text and "参照" not in text  # sources are shown for answers only


def test_follow_gets_the_demo_greeting():
    line = FakeLine()
    client, _ = make_line_client(line=line)
    with client:
        post(client, event("follow"))
    text = line.replies()[0][2][0]["text"]
    assert "こもれび整体院（デモ）" in text and "架空" in text and "個人情報" in text
    assert "スタッフからの返信はできません" in text


@pytest.mark.parametrize("message", [{"type": "sticker", "id": "1", "packageId": "1", "stickerId": "1"},
                                     {"type": "image", "id": "2"}])
def test_non_text_message_gets_a_fixed_reply(message):
    provider, line = FakeProvider(), FakeLine()
    client, _ = make_line_client(provider, line=line)
    with client:
        post(client, event(message=message))
    assert line.replies()[0][2][0]["text"] == NON_TEXT_REPLY
    assert provider.calls == 0


def test_ask_postback_prompts_for_a_question():
    line = FakeLine()
    client, _ = make_line_client(line=line)
    with client:
        post(client, event("postback", postback={"data": "action=ask"}))
    assert line.replies()[0][2][0]["text"] == ASK_REPLY


@pytest.mark.parametrize("source", [{"type": "group", "groupId": "Cabc", "userId": USER},
                                    {"type": "room", "roomId": "Rabc"}])
def test_group_and_room_events_are_ignored(retrieved, source):
    provider, line = FakeProvider(), FakeLine()
    client, _ = make_line_client(provider, line=line)
    with client:
        assert post(client, event(source=source)).status_code == 200
    assert line.calls == [] and provider.calls == 0


def test_unfollow_is_ignored():
    line = FakeLine()
    client, _ = make_line_client(line=line)
    with client:
        post(client, {"type": "unfollow", "timestamp": int(NOW * 1000), "source": {"type": "user", "userId": USER}})
    assert line.calls == []


def test_too_long_message_gets_a_polite_reply(retrieved):
    provider, line = FakeProvider(), FakeLine()
    client, _ = make_line_client(provider, line=line)
    with client:
        post(client, event(text="あ" * 301))
    assert "300文字以内" in line.replies()[0][2][0]["text"]
    assert provider.calls == 0


# --- reply only, expiry ------------------------------------------------------------------------------

def test_client_can_only_reply_and_show_loading():
    public = {name for name in dir(LineClient) if not name.startswith("_")}
    assert public == {"reply", "show_loading"}


def test_no_push_endpoint_anywhere_in_the_code():
    forbidden = ("/message/push", "multicast", "broadcast", "narrowcast")
    for path in [*(ROOT_DIR / "src").rglob("*.py"), *(ROOT_DIR / "scripts").rglob("*.py")]:
        text = path.read_text(encoding="utf-8").lower()
        for word in forbidden:
            assert word not in text, f"{word} in {path.relative_to(ROOT_DIR)}"


def _http_line(status=200, body=None):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["authorization"], json.loads(request.content)))
        if "reply" in str(request.url):
            return httpx.Response(status, json=body or {})
        return httpx.Response(200, json={})

    return LineClient(TOKEN, httpx.Client(transport=httpx.MockTransport(handler))), seen


def test_real_client_calls_only_the_reply_and_loading_endpoints(retrieved):
    line, seen = _http_line()
    client, _ = make_line_client(line=line)
    with client:
        post(client, event(token="rt-9"), event("follow", token="rt-10"))
    assert [url for url, _, _ in seen] == [LOADING_URL, REPLY_URL, REPLY_URL]
    assert all(auth == f"Bearer {TOKEN}" for _, auth, _ in seen)
    assert seen[1][2]["replyToken"] == "rt-9" and seen[2][2]["replyToken"] == "rt-10"


def test_invalid_reply_token_is_logged_and_dropped(retrieved, caplog):
    line, seen = _http_line(400, {"message": "Invalid reply token"})
    with caplog.at_level(logging.INFO):
        client, _ = make_line_client(line=line)
        with client:
            assert post(client, event()).status_code == 200
    assert [url for url, _, _ in seen] == [LOADING_URL, REPLY_URL]  # no retry, no push
    assert _line_logs(caplog)[-1]["reply"] == "token_invalid"


def test_other_api_errors_are_logged_by_status(retrieved, caplog):
    line, _ = _http_line(500, {"message": "boom"})
    with caplog.at_level(logging.INFO):
        client, _ = make_line_client(line=line)
        with client:
            post(client, event())
    assert _line_logs(caplog)[-1]["reply"] == "error:500"


def test_expired_event_makes_no_llm_call_and_no_reply(retrieved, caplog):
    provider, line = FakeProvider(), FakeLine()
    with caplog.at_level(logging.INFO):
        client, _ = make_line_client(provider, line=line)
        with client:
            post(client, event(sent=NOW - 51))
    assert provider.calls == 0 and line.calls == []
    assert _line_logs(caplog)[-1]["reply"] == "expired"


def test_reply_is_dropped_if_the_answer_took_too_long(retrieved, caplog):
    # The event was fresh when it arrived but the deadline passed while answering.
    times = iter([NOW, NOW + 55])
    provider, line = FakeProvider(), FakeLine()
    settings = AppSettings(app_today="2026-10-24", line_channel_secret=SECRET, line_channel_access_token=TOKEN)
    app = src.app.create_app(settings, provider, fake_connect, FakeUsage(), warm=False, line_client=line,
                             line_clock=lambda: next(times))
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        post(client, event())
    assert provider.calls == 1 and line.replies() == []
    assert _line_logs(caplog)[-1]["reply"] == "expired"


# --- limits ------------------------------------------------------------------------------------------

def test_rate_limit_is_per_line_user(retrieved, caplog):
    line = FakeLine()
    with caplog.at_level(logging.INFO):
        client, limiter = make_line_client(line=line)
        with client:
            for i in range(10):
                post(client, event(token=f"a{i}"))
            post(client, event(token="a10"))
            post(client, event(user=OTHER_USER, token="b0"))
    texts = {token: messages[0]["text"] for _, token, messages in line.replies()}
    assert all(texts[f"a{i}"].startswith("60分") for i in range(10))
    assert texts["a10"] == RATE_LIMITED_REPLY
    assert texts["b0"].startswith("60分")
    assert len(limiter._windows) == 2 and all(len(key) == 64 for key in limiter._windows)
    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert USER not in everything and OTHER_USER not in everything


def test_line_and_web_limits_are_separate(retrieved):
    client, _ = make_line_client(rate_limit_per_minute=1)
    with client:
        assert client.post("/chat", json={"tenant": "komorebi-seitai-demo", "message": "料金は？"}).status_code == 200
        line_ok = post(client, event())
    assert line_ok.status_code == 200


def test_rate_limited_wording_matches_the_web_chat():
    assert RATE_LIMITED_REPLY == src.app.RATE_LIMITED_DETAIL


def test_daily_cap_is_shared_with_the_web_chat(retrieved, caplog):
    provider, line = FakeProvider(), FakeLine()
    with caplog.at_level(logging.INFO):
        client, _ = make_line_client(provider, line=line, daily_question_cap=2)
        with client:
            client.post("/chat", json={"tenant": "komorebi-seitai-demo", "message": "料金は？"})
            post(client, event(token="t1"))
            post(client, event(token="t2"))
    assert provider.calls == 2
    assert line.replies()[-1][2][0]["text"] == BUDGET_REPLY
    assert "LINE" not in BUDGET_REPLY and "電話" in BUDGET_REPLY
    chat_logs = [json.loads(r.getMessage()) for r in caplog.records if '"event": "chat"' in r.getMessage()]
    assert [log["channel"] for log in chat_logs] == ["web", "line", "line"]
    assert chat_logs[-1]["outcome"] == "cap"


# --- privacy -----------------------------------------------------------------------------------------

def test_logs_hold_no_user_id_token_signature_or_raw_pii(retrieved, caplog):
    message = "090-1234-5678 に連絡ください。60分はいくら？"
    body = json.dumps({"events": [event(text=message, token="rt-secret-token")]}, ensure_ascii=False).encode()
    with caplog.at_level(logging.INFO):
        client, _ = make_line_client()
        with client:
            post(client, body=body)
    everything = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (USER, "rt-secret-token", sign(body), SECRET, TOKEN, "090-1234-5678"):
        assert secret not in everything
    chat = [json.loads(r.getMessage()) for r in caplog.records if '"event": "chat"' in r.getMessage()][-1]
    assert chat["question"] == "[電話番号] に連絡ください。60分はいくら？" and chat["channel"] == "line"


def test_settings_repr_hides_the_secrets():
    text = repr(AppSettings(line_channel_secret=SECRET, line_channel_access_token=TOKEN))
    assert SECRET not in text and TOKEN not in text


# --- formatting --------------------------------------------------------------------------------------

LABELS = {"01_menu_prices": "料金・メニュー", "07_news": "お知らせ"}


def test_format_strips_markdown():
    answer = "## 料金\n**全身整体60分**は7,150円です。\n\n\n\n- 初検料：1,100円\n* 詳しくは[料金表](https://x.example)をご覧ください。"
    assert format_for_line(answer, "handoff", [], LABELS) == (
        "料金\n全身整体60分は7,150円です。\n\n・初検料：1,100円\n・詳しくは料金表をご覧ください。")


def test_format_adds_source_labels_once_for_answers():
    assert format_for_line("7,150円です。", "answer", ["01_menu_prices", "07_news", "01_menu_prices"], LABELS) == (
        "7,150円です。\n\n（参照：料金・メニュー／お知らせ）")
    assert format_for_line("確認いたします。", "handoff", ["01_menu_prices"], LABELS) == "確認いたします。"
    assert format_for_line("7,150円です。", "answer", [], LABELS) == "7,150円です。"


def test_format_caps_the_length():
    text = format_for_line("あ" * 6000, "answer", ["01_menu_prices"], LABELS)
    assert len(text) == 5000 and text.endswith("…")


def test_quick_reply_labels_fit_line_limits():
    site = json.loads(Path(ROOT_DIR / "data/komorebi-seitai-demo/site.json").read_text(encoding="utf-8"))
    line = site["suggestions"]["line"]
    assert 3 <= len(line) <= 4
    assert all(len(q) <= QUICK_REPLY_LABEL_CHARS for q in line)
