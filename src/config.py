"""Settings loaded from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
EVAL_DIR = ROOT_DIR / "eval"

DEFAULT_TENANT = "komorebi-seitai-demo"
EMBED_DIM = 1024
TOP_K = 5
MAX_TOKENS = 500


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    aws_region: str
    bedrock_model_id: str
    database_url: str
    embed_model: str

    @property
    def model_id(self) -> str:
        """The model ID actually sent to the active provider."""
        return self.bedrock_model_id if self.llm_provider == "bedrock" else self.llm_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(ROOT_DIR / ".env")
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "anthropic").strip().lower(),
        llm_model=os.getenv("LLM_MODEL", "claude-haiku-5-5"),
        aws_region=os.getenv("AWS_REGION", "us-east-2"),
        bedrock_model_id=os.getenv("BEDROCK_MODEL_ID", ""),
        database_url=os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/frontdesk"
        ),
        embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-m3"),
    )


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def _list_env(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(v.strip() for v in os.getenv(name, default).split(",") if v.strip())


@dataclass(frozen=True)
class AppSettings:
    """API limits and behavior (src/app.py); documented in .env.example."""

    allowed_tenants: tuple[str, ...] = (DEFAULT_TENANT,)
    max_message_chars: int = 300
    rate_limit_per_minute: int = 10
    rate_limit_per_day: int = 50
    daily_question_cap: int = 500
    daily_token_budget: int = 2_000_000  # input + output tokens per JST day
    app_today: str = ""  # empty: the real date in Asia/Tokyo; set (e.g. 2026-10-24) to pin it like eval
    trust_proxy_headers: bool = False  # use X-Forwarded-For (only behind a trusted proxy/load balancer)
    cors_origins: tuple[str, ...] = ()
    demo_autosend: bool = False  # let the web page auto-send ?demo=q1|q2 (screenshots / demo video only)
    extra_frame_ancestors: tuple[str, ...] = ()  # local testing only: more sites that may frame /embed


def get_app_settings() -> AppSettings:
    load_dotenv(ROOT_DIR / ".env")
    return AppSettings(
        allowed_tenants=_list_env("ALLOWED_TENANTS", DEFAULT_TENANT),
        max_message_chars=_int_env("MAX_MESSAGE_CHARS", 300),
        rate_limit_per_minute=_int_env("RATE_LIMIT_PER_MINUTE", 10),
        rate_limit_per_day=_int_env("RATE_LIMIT_PER_DAY", 50),
        daily_question_cap=_int_env("DAILY_QUESTION_CAP", 500),
        daily_token_budget=_int_env("DAILY_TOKEN_BUDGET", 2_000_000),
        app_today=os.getenv("APP_TODAY", "").strip(),
        trust_proxy_headers=_bool_env("TRUST_PROXY_HEADERS"),
        cors_origins=_list_env("CORS_ORIGINS"),
        demo_autosend=_bool_env("DEMO_AUTOSEND"),
        extra_frame_ancestors=_list_env("EXTRA_FRAME_ANCESTORS"),
    )
