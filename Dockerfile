# frontdesk-ai API: FastAPI + bge-m3 (CPU). The embedding model is baked into the image so a
# container starts without downloading ~2.3 GB.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

WORKDIR /app

# CPU-only torch first, so sentence-transformers doesn't pull the multi-GB CUDA build.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch
COPY requirements.txt .
RUN pip install -r requirements.txt

ARG EMBED_MODEL=BAAI/bge-m3
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')"
# Never call the Hugging Face Hub at runtime.
ENV HF_HUB_OFFLINE=1

COPY src/ src/
COPY data/ data/

RUN useradd --create-home --uid 10001 app && chown -R app /app

# The release, reported by GET /health (set by the deploy workflow). Kept last so a new tag
# doesn't invalidate the cached torch/model layers.
ARG APP_VERSION=dev
ARG GIT_SHA=unknown
ENV APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA}
LABEL org.opencontainers.image.source="https://github.com/shomas383/frontdesk-ai" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"

USER app

EXPOSE 8000
# One worker on purpose: the per-IP rate limiter lives in process memory (src/limits.py).
# No access log: it would print client IPs.
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
