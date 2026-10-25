# frontdesk-ai — project brief

A Japanese AI front desk for small clinics (整体院・鍼灸院). It answers from the clinic's own Markdown docs with citations, hands off to staff when the docs don't say, and never gives medical advice (`answer` / `handoff` / `refuse_redirect`). The point of the project is measured improvement: every change is a before/after on the eval set.

## Status
- **Milestone 1 (baseline RAG + eval harness): complete.** fix-v2 got 100% on both behavior and hand labels for the 50 original questions.
- **Milestone 2 (retrieval quality): in progress.** The benchmark is now harder: the FAQ is split per Q&A, there are noise docs 08–11, and there are 20 held-out questions (q51–q70). Next comes re-baselining, then hybrid keyword + dense search and reranking, judged by section recall@1 and MRR. The data note is in [notes/MILESTONE2_DATA.md](notes/MILESTONE2_DATA.md).

## Stack and decisions
- LLM: Claude Haiku 5.5 (`claude-haiku-5-5`) through the provider interface in `src/llm.py`. `LLM_PROVIDER=anthropic` (default) or `bedrock`. Thinking off, effort low, max_tokens 500, structured JSON output.
- Embeddings: `BAAI/bge-m3` (1024-dim, normalized, CPU). Retrieval modes (`--retrieval`): `dense` (default; top-5 cosine) `hybrid` (dense top-20 + BM25 top-20 → RRF k=60 → top 5) and `rerank` (the hybrid top-20 reordered by `BAAI/bge-reranker-v2-m3`, local CPU, ~8 s/question → top 5).
- Keyword search: pure-Python BM25 with SudachiPy (core dict, mode A, normalized forms) in `src/lexical.py`. pg_bigm and PGroonga aren't packaged for the `pgvector/pgvector:pg17` image (checked 2026-10-24), so they'd need a custom Dockerfile.
- DB: Postgres 17 + pgvector in Docker on **host port 5433**. 5432 belongs to another project; don't stop other containers.
- Chunking: one chunk per `##` section, `chunk_id = <doc_id>#<section>`, with the doc preamble repeated in every chunk.
- Multi-tenant: docs live in `data/<tenant>/docs/*.md`, and every chunk row stores `tenant_id`. The demo tenant is `komorebi-seitai-demo`.

## Environment (Windows)
- PowerShell 5.1 is the main shell; Git Bash is also available.
- Python: the conda env `frontdesk-ai` at `C:\Users\Shoma\miniconda3\envs\frontdesk-ai\python.exe`. There is no system Python on PATH, so call this path directly.
- Set `PYTHONIOENCODING=utf-8` for Japanese output. Don't pipe JSON through PowerShell 5.1, because it adds a BOM.

## Repo layout
| Path | What |
|---|---|
| `src/chunking.py`, `src/ingest.py` | split docs → embed → upsert (re-runnable, removes stale chunks) |
| `src/retrieval.py`, `src/lexical.py`, `src/rerank.py` | retrieval modes + RRF; tokenizer + BM25; cross-encoder reranker |
| `src/answer.py`, `src/prompts.py`, `src/llm.py` | retrieval + LLM call, the Japanese system prompt + JSON schema, providers |
| `src/eval.py`, `src/metrics.py`, `src/compare.py` | eval runner, pure metric functions, run-vs-run diff |
| `data/<tenant>/docs/` | clinic docs (source of truth for answers) |
| `data/<tenant>/doc_meta.json`, `src/doc_meta.py` | per-doc/section `doc_type` (official, news, blog, campaign) + dates, rendered as `<chunk>` attributes for the LLM; not embedded. Keep it in sync when docs change (a test checks coverage) |
| `eval/questions.jsonl` | the 50 original questions (q01–q50) |
| `eval/questions_m2_heldout.jsonl` | the 20 held-out questions (q51–q70, `split: heldout_m2`) |
| `eval/runs/` | committed run outputs: `<ts>_<name>.jsonl` + `.summary.json` (+ `.compare.md`) |
| `tests/` | unit tests; no DB, API or model needed |
| `notes/` | data-drop notes and other reference material |

## Commands
```
python -m src.ingest --tenant komorebi-seitai-demo
python -m src.eval --name <name> [--questions eval/questions_m2_heldout.jsonl] [--retrieval dense|hybrid|rerank] [--limit 5]
python -m src.eval --resummarize eval/runs/<run>.jsonl
python -m src.compare eval/runs/<base>.jsonl eval/runs/<new>.jsonl [--carry-labels]
pytest
```

## Eval workflow
1. **Run** `src.eval --name ...` on one question file per run. The summary records `questions_file`, git commit/dirty and the prompt hash, and it reports per category and, when present, per `split`.
2. **Hand-label** by setting `correct` (and optionally `label_note`) in the run `.jsonl`, comparing against `expected_answer` and `trap`.
3. **Resummarize** with `--resummarize`, which recomputes metrics with no API calls. It refreshes `gold_chunks` from the run's own question file.
4. **Compare** with `src.compare`, which gives metric deltas, behavior flips and side-by-side changed answers. `--carry-labels` copies labels onto byte-identical answers.

## Rules
- **Commit before any run you want to cite**, so that `git_dirty` is false (`eval/runs/` is excluded from the dirty check).
- **Don't resummarize old runs against new labels.** For example, runs from before the M2 FAQ split retrieved `06_faq#よくある質問`, which no longer exists. Cite their stored summaries instead.
- **Hand labels come from the user.** Never fill in `correct` yourself; flag suspicious answers instead.
- Gold labels: a question's `gold_chunks` must exist in the corpus and fall within its `sources` (enforced by tests). Ask before changing labels that a data note doesn't specify.
- Don't change the prompt or retrieval in the same commit as a baseline run; one change per measured step.
- The eval pins "today" to 2026-10-24 (`--today`) because the docs' notices are relative to it.
