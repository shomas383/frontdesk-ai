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
