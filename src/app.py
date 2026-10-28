"""HTTP API for the front desk: the same answer path as eval, plus abuse and cost protection.

Run: uvicorn src.app:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
Single worker only: the per-IP rate limiter is in process memory (see src/limits.py).
The access log is off because it prints client IPs; each request is logged as one JSON line instead.

POST /chat {tenant, message} -> {answer, behavior, sources};  GET /health
GET / serves the web chat (src/static/); GET /api/config gives it the tenant, labels and limits.
GET /embed?tenant=... is the same chat in a compact layout, shown in an iframe by widget.js.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import db
from src.answer import FALLBACK_ANSWER, answer_question
from src.config import DATA_DIR, AppSettings, get_app_settings
from src.framing import frame_ancestors
from src.limits import RateLimiter, now_jst
from src.llm import LLMProvider, get_provider
from src.pricing import estimate_cost_usd
from src.prompts import prompt_hash
from src.redact import redact_pii
from src.retrieval import warm_up
from src.usage import PostgresUsage

# Shown when the daily question cap or token budget is used up; the customer contacts the clinic directly.
BUDGET_FALLBACK_ANSWER = (
    "申し訳ございません。ただいま自動応答をご利用いただけません。"
    "お手数ですが、お電話またはLINEにて当院へ直接お問い合わせください。"
)
RATE_LIMITED_DETAIL = "ただいまご質問が集中しております。恐れ入りますが、しばらく時間をおいてから再度お試しください。"
EMPTY_MESSAGE_DETAIL = "ご質問を入力してください。"

request_log = logging.getLogger("frontdesk.requests")


def build_info() -> dict[str, str]:
    """The release this process runs, baked into the image by the Dockerfile's build args."""
    return {"version": os.getenv("APP_VERSION", "dev"), "git_sha": os.getenv("GIT_SHA", "unknown")}


STATIC_DIR = Path(__file__).resolve().parent / "static"
# Everything the page needs is same-origin and none of it is inline, so the CSP can be strict.
CSP_DIRECTIVES = {
    "default-src": "'self'",
    "img-src": "'self' data:",
    "frame-ancestors": "'none'",
    "base-uri": "'none'",
    "form-action": "'none'",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def content_security_policy(frame_ancestors: str = "'none'") -> str:
    return "; ".join(f"{name} {value}" for name, value in
                     {**CSP_DIRECTIVES, "frame-ancestors": frame_ancestors}.items())


# Sent on every response except a successful /embed, which sets its own CSP listing the tenant's sites.
# X-Frame-Options is for old browsers; it can't list sites, so /embed doesn't get it.
NO_FRAMING_HEADERS = {
    "Content-Security-Policy": content_security_policy(),
    "X-Frame-Options": "DENY",
}


class ChatRequest(BaseModel):
    tenant: str
    message: str


class ChatResponse(BaseModel):
    answer: str
    behavior: str
    sources: list[str]


def _setup_logging() -> None:
    """One JSON object per line on stdout (picked up by Docker / CloudWatch as-is)."""
    if not request_log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        request_log.addHandler(handler)
        request_log.setLevel(logging.INFO)


def _read_site(tenant_id: str) -> dict[str, Any]:
    path = DATA_DIR / tenant_id / "site.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_site(tenant_id: str) -> dict[str, Any]:
    """Display info for the web page (data/<tenant>/site.json): clinic_name, doc_id -> label, and the
    suggested questions per mode ("page": the full-page demo, "embed": the chat on the clinic's site)."""
    site = _read_site(tenant_id)
    return {"clinic_name": site.get("clinic_name", tenant_id), "doc_labels": site.get("doc_labels", {}),
            "suggestions": site.get("suggestions", {})}


def load_embed_origins(tenant_id: str) -> list[str]:
    """The clinic's own sites, which may show /embed in an iframe (site.json "embed_origins")."""
    return list(_read_site(tenant_id).get("embed_origins", []))


def log_event(**fields: Any) -> None:
    """Log one event. Never pass client identifiers (IP, user agent, session) here."""
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields}
    request_log.info(json.dumps(record, ensure_ascii=False))


def create_app(
    settings: AppSettings | None = None,
    provider: LLMProvider | None = None,
    connect: Callable[[], AbstractContextManager[Any]] = db.connect,
    usage: Any = None,
    limiter: RateLimiter | None = None,
    warm: bool = True,
) -> FastAPI:
    settings = settings or get_app_settings()
    usage = usage or PostgresUsage()
    limiter = limiter or RateLimiter(settings.rate_limit_per_minute, settings.rate_limit_per_day)
    _setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.provider = provider or get_provider()
        with connect() as conn:
            usage.ensure_schema(conn)
            if warm:  # load bge-m3 now rather than on the first customer question
                warm_up(conn, settings.allowed_tenants[0], "dense")
        log_event(event="startup", **build_info(), model=app.state.provider.model_id,
                  prompt_hash=prompt_hash(),
                  tenants=list(settings.allowed_tenants), max_message_chars=settings.max_message_chars,
                  rate_limit_per_minute=settings.rate_limit_per_minute,
                  rate_limit_per_day=settings.rate_limit_per_day,
                  daily_question_cap=settings.daily_question_cap,
                  daily_token_budget=settings.daily_token_budget, app_today=settings.app_today or None)
        yield

    app = FastAPI(title="frontdesk-ai", lifespan=lifespan)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if "content-security-policy" not in response.headers:  # only a successful /embed sets its own
            response.headers.update(NO_FRAMING_HEADERS)
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/embed", include_in_schema=False)
    def embed(tenant: str = "") -> FileResponse:
        """The compact chat that widget.js shows in an iframe on a clinic's own site."""
        if tenant not in settings.allowed_tenants:
            raise HTTPException(404, detail="unknown tenant")
        origins = [*load_embed_origins(tenant), *settings.extra_frame_ancestors]
        csp = content_security_policy(frame_ancestors(origins))
        return FileResponse(STATIC_DIR / "embed.html", headers={"Content-Security-Policy": csp})

    @app.get("/api/config")
    def page_config(tenant: str | None = None) -> dict[str, Any]:
        """What the web page needs: the given tenant (embed page) or the first allowed one (full page)."""
        if tenant is None:
            tenant = settings.allowed_tenants[0]
        elif tenant not in settings.allowed_tenants:
            raise HTTPException(404, detail="unknown tenant")
        return {"tenant": tenant, **load_site(tenant), "max_message_chars": settings.max_message_chars,
                "demo_autosend": settings.demo_autosend}

    def client_ip(request: Request) -> str:
        if settings.trust_proxy_headers:
            forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
            if forwarded:  # right-most entry: the one our own proxy appended
                return forwarded[-1]
        return request.client.host if request.client else "unknown"

    def today() -> str:
        return settings.app_today or now_jst().date().isoformat()

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            with connect() as conn:
                conn.execute("SELECT 1")
            db_status = "ok"
        except Exception:
            db_status = "error"
        body = {"status": "ok" if db_status == "ok" else "degraded", "db": db_status,
                "model": app.state.provider.model_id, **build_info()}
        return JSONResponse(body, status_code=200 if db_status == "ok" else 503)

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, request: Request) -> ChatResponse:
        started = time.perf_counter()
        tenant, message = req.tenant, req.message.strip()
        if tenant not in settings.allowed_tenants:
            log_event(event="chat", outcome="invalid", reason="unknown_tenant")
            raise HTTPException(404, detail="unknown tenant")
        if not message or len(message) > settings.max_message_chars:
            log_event(event="chat", tenant=tenant, outcome="invalid",
                      reason="empty" if not message else "too_long", message_chars=len(message))
            detail = EMPTY_MESSAGE_DETAIL if not message else (
                f"ご質問は{settings.max_message_chars}文字以内でご入力ください。")
            raise HTTPException(422, detail=detail)

        allowed, retry_after = limiter.check(client_ip(request))
        if not allowed:
            log_event(event="chat", tenant=tenant, outcome="rate_limited")
            raise HTTPException(429, detail=RATE_LIMITED_DETAIL, headers={"Retry-After": str(retry_after)})

        day = now_jst().date()  # budgets follow the real JST day even when APP_TODAY pins the docs' date
        fields: dict[str, Any] = {"event": "chat", "tenant": tenant, "question": redact_pii(message)}
        try:
            with connect() as conn:
                refused = usage.reserve(conn, day, settings.daily_question_cap, settings.daily_token_budget)
                if refused:
                    log_event(**fields, outcome=refused, behavior="handoff", sources=[],
                              latency_ms=int((time.perf_counter() - started) * 1000))
                    return ChatResponse(answer=BUDGET_FALLBACK_ANSWER, behavior="handoff", sources=[])

                provider = app.state.provider
                result = answer_question(conn, provider, tenant, message, today())
                cost = estimate_cost_usd(provider.model_id, result.input_tokens, result.output_tokens)
                usage.record(conn, day, result.input_tokens, result.output_tokens, cost or 0.0)
        except Exception as exc:  # DB down, unexpected provider failure, ...: staff takes over
            log_event(**fields, outcome="error", behavior="handoff", sources=[],
                      error=f"{type(exc).__name__}: {exc}",
                      latency_ms=int((time.perf_counter() - started) * 1000))
            return ChatResponse(answer=FALLBACK_ANSWER, behavior="handoff", sources=[])

        guardrail = result.guardrail or {}
        log_event(
            **fields, outcome="error" if result.error else "answered",
            behavior=result.behavior, sources=result.sources,
            guardrail_fired=bool(guardrail.get("fired")),
            guardrail_removed=guardrail.get("removed_sentences") or [],
            latency_ms=int((time.perf_counter() - started) * 1000), llm_latency_ms=result.llm_latency_ms,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens, cost_usd=cost,
            error=result.error,
        )
        return ChatResponse(answer=result.answer, behavior=result.behavior, sources=result.sources)

    return app


app = create_app()
