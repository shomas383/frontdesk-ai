import inspect
import json
import logging
from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import src.answer
import src.app
from src.answer import FALLBACK_ANSWER, answer_question
from src.chunking import load_tenant_chunks
from src.config import DATA_DIR, DEFAULT_TENANT, AppSettings
from src.db import RetrievedChunk
from src.eval import build_parser
from src.limits import JST, RateLimiter
from src.llm import LLMResult
from src.retrieval import Retrieval

CHUNKS = {c.chunk_id: c for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs")}
GIFT_CLAIM = ("ギフト券のご用意はございません。2025年の年末に限り販売していた全身整体60分のご利用券は、"
              "現在は終了しております。ご不明な点はスタッフがご案内いたします。")


class FakeProvider:
    model_id = "claude-haiku-5-5"

    def __init__(self, data=None, exc=None):
        self.data = data or {"answer": "60分の全身整体は6,600円（税込）です。", "behavior": "answer",
                             "sources": ["01_menu_prices"]}
        self.exc = exc
        self.calls = 0

    def generate_json(self, system, user, schema, max_tokens):
        self.calls += 1
        if self.exc:
            raise self.exc
        return LLMResult(self.data, json.dumps(self.data, ensure_ascii=False), "end_turn", 3000, 100)


class FakeUsage:
    """In-memory stand-in for src.usage.PostgresUsage (same interface, same refusal rules)."""

    def __init__(self):
        self.questions = 0
        self.tokens = 0

    def ensure_schema(self, conn):
        pass

    def reserve(self, conn, day, cap, budget):
        if self.questions >= cap:
            return "cap"
        if self.tokens >= budget:
            return "budget"
        self.questions += 1
        return None

    def record(self, conn, day, input_tokens, output_tokens, cost_usd):
        self.tokens += input_tokens + output_tokens


class FakeConn:
    def execute(self, *args):
        return self


@contextmanager
def fake_connect():
    yield FakeConn()


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.wall = datetime(2026, 10, 25, 12, 0, tzinfo=JST)

    def __call__(self):
        return self.t


def _retrieval(*chunk_ids):
    chunks = [RetrievedChunk(c.chunk_id, c.doc_id, c.section, c.text, 0.5) for c in map(CHUNKS.get, chunk_ids)]
    return Retrieval(chunks, [{"chunk_id": c.chunk_id, "doc_id": c.doc_id} for c in chunks], list(chunk_ids))


@pytest.fixture
def retrieved(monkeypatch):
    """Canned retrieval (no DB / embedding model); everything after it is the real answer path."""
    ids = ["01_menu_prices#コース料金", "02_hours#営業時間"]
    monkeypatch.setattr(src.answer, "retrieve", lambda conn, tenant, question, mode, k: _retrieval(*ids))
    return ids


def make_client(provider=None, usage=None, clock=None, **overrides):
    settings = AppSettings(**{"app_today": "2026-10-24", **overrides})
    clock = clock or Clock()
    limiter = RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_per_day, clock, lambda: clock.wall)
    app = src.app.create_app(settings, provider or FakeProvider(), fake_connect, usage or FakeUsage(), limiter,
                             warm=False)
    return TestClient(app)


def _chat(client, message="60分の全身整体はいくらですか？", tenant=DEFAULT_TENANT, **kw):
    return client.post("/chat", json={"tenant": tenant, "message": message}, **kw)


def _chat_logs(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records
            if r.name == "frontdesk.requests" and '"event": "chat"' in r.getMessage()]


def test_chat_response_shape(retrieved):
    with make_client() as client:
        res = _chat(client)
    assert res.status_code == 200
    assert res.json() == {"answer": "60分の全身整体は6,600円（税込）です。", "behavior": "answer",
                          "sources": ["01_menu_prices"]}


def test_health(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    with make_client() as client:
        res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "ok", "model": "claude-haiku-5-5",
                          "version": "dev", "git_sha": "unknown"}


def test_health_reports_the_release(monkeypatch):
    # The deploy workflow waits until /health shows the tag it just pushed.
    monkeypatch.setenv("APP_VERSION", "v0.4.0")
    monkeypatch.setenv("GIT_SHA", "abc1234")
    with make_client() as client:
        body = client.get("/health").json()
    assert (body["version"], body["git_sha"]) == ("v0.4.0", "abc1234")


def test_app_uses_the_eval_defaults(retrieved, monkeypatch):
    calls = []

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return answer_question(*args, **kwargs)

    monkeypatch.setattr(src.app, "answer_question", spy)
    with make_client() as client:
        assert _chat(client).status_code == 200
    assert calls == [{}]  # no feature overrides: answer_question's own defaults apply
    defaults = {n: p.default for n, p in inspect.signature(answer_question).parameters.items()}
    args = build_parser().parse_args(["--name", "x"])
    assert defaults["retrieval"] == args.retrieval == "dense"
    assert defaults["doc_meta"] is args.doc_meta is False
    assert defaults["guardrail"] is args.guardrail is True
    assert defaults["small_docs"] is args.small_docs is True


def test_guardrail_runs_in_the_api(monkeypatch, caplog):
    monkeypatch.setattr(src.answer, "retrieve",
                        lambda conn, tenant, question, mode, k: _retrieval("10_campaign_2025#年末ギフトのご案内"))
    provider = FakeProvider({"answer": GIFT_CLAIM, "behavior": "answer", "sources": ["10_campaign_2025"]})
    with caplog.at_level(logging.INFO), make_client(provider) as client:
        body = _chat(client, "ギフト券はありますか？").json()
    assert body["behavior"] == "handoff"
    assert "ギフト券のご用意はございません" not in body["answer"]
    log = _chat_logs(caplog)[-1]
    assert log["guardrail_fired"] is True and log["guardrail_removed"]


def test_small_docs_expansion_runs_in_the_api(retrieved):
    provider = FakeProvider()
    seen = {}
    original = provider.generate_json

    def capture(system, user, schema, max_tokens):
        seen["user"] = user
        return original(system, user, schema, max_tokens)

    provider.generate_json = capture
    with make_client(provider) as client:
        _chat(client)
    assert "## 初検料" in seen["user"]  # full 01_menu_prices doc, not just the retrieved section


@pytest.mark.parametrize("message", ["あ" * 301, "", "   "])
def test_message_length_limit(retrieved, message):
    provider = FakeProvider()
    with make_client(provider) as client:
        res = _chat(client, message)
    assert res.status_code == 422
    assert provider.calls == 0


def test_message_at_the_limit_is_accepted(retrieved):
    with make_client() as client:
        assert _chat(client, "あ" * 300).status_code == 200


@pytest.mark.parametrize("tenant", ["other-clinic", "../komorebi-seitai-demo", ""])
def test_unknown_tenant(retrieved, tenant):
    with make_client() as client:
        assert _chat(client, tenant=tenant).status_code == 404


def test_rate_limit_per_minute(retrieved):
    clock = Clock()
    with make_client(clock=clock) as client:
        assert all(_chat(client).status_code == 200 for _ in range(10))
        res = _chat(client)
        assert res.status_code == 429
        assert 1 <= int(res.headers["Retry-After"]) <= 60
        assert "しばらく時間をおいて" in res.json()["detail"]
        clock.t += 61
        assert _chat(client).status_code == 200


def test_rate_limit_per_day(retrieved):
    clock = Clock()
    with make_client(clock=clock) as client:
        for _ in range(50):
            assert _chat(client).status_code == 200
            clock.t += 7  # stay under 10/min
        res = _chat(client)
        assert res.status_code == 429
        assert int(res.headers["Retry-After"]) == 12 * 3600  # until JST midnight
        clock.wall = clock.wall.replace(day=26, hour=0, minute=1)
        assert _chat(client).status_code == 200


def test_rate_limit_is_per_client(retrieved):
    with make_client(trust_proxy_headers=True, rate_limit_per_minute=1) as client:
        assert _chat(client, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200
        assert _chat(client, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 429
        assert _chat(client, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 200


def test_daily_question_cap_returns_polite_handoff(retrieved):
    provider = FakeProvider()
    with make_client(provider, daily_question_cap=2) as client:
        _chat(client), _chat(client)
        res = _chat(client)
    assert res.status_code == 200
    assert res.json() == {"answer": src.app.BUDGET_FALLBACK_ANSWER, "behavior": "handoff", "sources": []}
    assert provider.calls == 2


def test_token_budget_returns_polite_handoff(retrieved, caplog):
    provider = FakeProvider()  # 3,100 tokens per answer
    with caplog.at_level(logging.INFO), make_client(provider, daily_token_budget=5000) as client:
        assert _chat(client).json()["behavior"] == "answer"
        assert _chat(client).json()["behavior"] == "answer"  # 3,100 < 5,000: still allowed
        res = _chat(client)
    assert res.json()["answer"] == src.app.BUDGET_FALLBACK_ANSWER and res.json()["behavior"] == "handoff"
    assert "電話" in res.json()["answer"] and "LINE" in res.json()["answer"]
    assert provider.calls == 2
    assert _chat_logs(caplog)[-1]["outcome"] == "budget"


def test_pipeline_error_falls_back_to_handoff(retrieved, caplog):
    provider = FakeProvider(exc=RuntimeError("boom"))
    with caplog.at_level(logging.INFO), make_client(provider) as client:
        res = _chat(client)
    assert res.status_code == 200
    assert res.json() == {"answer": FALLBACK_ANSWER, "behavior": "handoff", "sources": []}
    log = _chat_logs(caplog)[-1]
    assert log["outcome"] == "error" and "boom" in log["error"]


def test_request_log_fields_and_privacy(retrieved, caplog):
    message = "予約したいです。090-1234-5678 か taro@example.com に連絡ください。60分はいくら？"
    with caplog.at_level(logging.INFO), make_client(trust_proxy_headers=True) as client:
        _chat(client, message, headers={"X-Forwarded-For": "198.51.100.77", "User-Agent": "secret-agent/1.0"})
    log = _chat_logs(caplog)[-1]
    assert log["question"] == "予約したいです。[電話番号] か [メール] に連絡ください。60分はいくら？"
    assert log["behavior"] == "answer" and log["sources"] == ["01_menu_prices"]
    assert log["guardrail_fired"] is False
    assert log["input_tokens"] == 3000 and log["output_tokens"] == 100
    assert log["cost_usd"] == pytest.approx((3000 * 0.10 + 100 * 0.50) / 1e6)
    assert isinstance(log["latency_ms"], int) and "llm_latency_ms" in log
    everything = "\n".join(r.getMessage() for r in caplog.records)
    for secret in ("198.51.100.77", "testclient", "secret-agent", "090-1234-5678", "taro@example.com"):
        assert secret not in everything


def test_rate_limiter_window():
    clock = Clock()
    limiter = RateLimiter(2, 100, clock, lambda: clock.wall)
    assert limiter.check("a") == (True, 0)
    clock.t += 30
    assert limiter.check("a") == (True, 0)
    assert limiter.check("a") == (False, 30)  # oldest request leaves the window 30 s from now
    clock.t += 30
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True
    assert all(len(key) == 64 for key in limiter._windows)  # keys are salted hashes, never raw IPs
