# frontdesk-ai — project brief

A Japanese AI front desk for small clinics (整体院・鍼灸院). It answers from the clinic's own Markdown docs with citations, hands off to staff when the docs don't say, and never gives medical advice (`answer` / `handoff` / `refuse_redirect`). The point of the project is measured improvement: every change is a before/after on the eval set.

## Status
- **Milestone 1 (baseline RAG + eval harness): complete** (tag `milestone-1`). fix-v2 got 100% on both behavior and hand labels for the 50 original questions.
- **Milestone 2 (retrieval quality on a harder benchmark): complete** (tag `milestone-2`). The results table is in README.md and the data note in [notes/MILESTONE2_DATA.md](notes/MILESTONE2_DATA.md).
  - Final configuration `m2-guardrail-v2`, 3 repeats:
    - hand-label 100% on both the original and held-out sets
    - behavior 100% (original) / 96.7% (held-out)
    - context recall 100%
  - Adopted, all on by default:
    - dense retrieval
    - prompt v2: the stale-content rule; medical questions always `refuse_redirect`
    - the unstated-negative guardrail
    - small-docs expansion of the price list
  - Tried and not adopted:
    - hybrid BM25+dense (`--retrieval hybrid`)
    - cross-encoder rerank (`--retrieval rerank`): raised r@1 by 8–12 points but hurt answers and cost 6× latency
    - doc metadata in the context (`--doc-meta`)
- **Milestone 3 (automatic LLM answer grader, calibrated against the hand labels): planned, deferred** so the public demo can come first. The milestone number is intentionally skipped for now.
- **Milestone 4 (public demo): in progress.** Web chat, LINE integration, AWS deployment (Bedrock) and a demo video.
  - Step 1 done: local API (`src/app.py`) + Dockerfile + compose `app` profile. See README "API (local)".
  - Step 2 done: web chat at `GET /` (`src/static/`, no build step or CDNs; strict CSP, so no inline script or style).

## Backlog (not started)
- **Unneeded staff follow-up:** answers that the official docs fully cover sometimes still add 「スタッフが確認のうえご案内」, which makes a correct answer sound uncertain. Example: 6,600円→7,150円, with `behavior` still `answer`.
  - Candidate prompt fix, measured as its own step (commit + 3 repeats on both sets).
  - Also count answer-behavior rows that contain handoff phrases.
  - Check that real handoffs (q36/q54/q64) and medical redirects don't regress.

## Default pipeline
question → bge-m3 → dense top-5 (pgvector) → small-docs expansion (01_menu_prices shown in full if any of its chunks is hit) → Claude (prompt v2, JSON: answer/behavior/sources) → guardrail → reply.

## Stack and decisions
- LLM: Claude Haiku 5.5 (`claude-haiku-5-5`) through the provider interface in `src/llm.py`. `LLM_PROVIDER=anthropic` (default) or `bedrock`. Thinking off, effort low, max_tokens 500, structured JSON output.
- Embeddings: `BAAI/bge-m3` (1024-dim, normalized, CPU). Retrieval modes (`--retrieval`):
  - `dense` (default): top-5 cosine
  - `hybrid`: dense top-20 + BM25 top-20 → RRF k=60 → top 5
  - `rerank`: the hybrid top-20 reordered by `BAAI/bge-reranker-v2-m3` (local, ~8 s/question on CPU) → top 5
- Keyword search: pure-Python BM25 with SudachiPy (core dict, mode A, normalized forms) in `src/lexical.py`. pg_bigm and PGroonga aren't packaged for the `pgvector/pgvector:pg17` image (checked 2026-10-24), so they'd need a custom Dockerfile.
- DB: Postgres 17 + pgvector in Docker on **host port 5433**. 5432 belongs to another project; don't stop other containers.
- Chunking: one chunk per `##` section, `chunk_id = <doc_id>#<section>`, with the doc preamble repeated in every chunk. The demo corpus has 51 chunks.
- Multi-tenant: docs live in `data/<tenant>/docs/*.md`, and every chunk row stores `tenant_id`. The demo tenant is `komorebi-seitai-demo`.

## Environment (Windows)
- PowerShell 5.1 is the main shell; Git Bash is also available.
- Python: the conda env `frontdesk-ai` at `C:\Users\Shoma\miniconda3\envs\frontdesk-ai\python.exe`. There is no system Python on PATH, so call this path directly.
- Set `PYTHONIOENCODING=utf-8` for Japanese output. Don't pipe JSON through PowerShell 5.1, because it adds a BOM.
- Multi-file patch scripts: write them to a file with the Write tool and then run them. Inline heredocs in the Bash tool can mangle backslashes (`\\r` became a carriage return once). To check for stray `\r`, read files with `newline=""`, because Python's `read_text` silently converts it.

## Repo layout
| Path | What |
|---|---|
| `src/chunking.py`, `src/ingest.py` | split docs → embed → upsert (re-runnable, removes stale chunks) |
| `src/retrieval.py`, `src/lexical.py`, `src/rerank.py` | retrieval modes + RRF; tokenizer + BM25; cross-encoder reranker; small-docs expansion |
| `data/<tenant>/retrieval.json` | `small_docs` (01_menu_prices): shown to the LLM in full when any of their chunks is retrieved. On by default; `--no-small-docs` turns it off. Retrieval metrics still use the real top 5; `context recall` measures what the LLM saw |
| `src/guardrails.py` | post-generation unstated-negative check (on by default; `--no-guardrail` turns it off); details below |
| `src/answer.py`, `src/prompts.py`, `src/llm.py` | retrieval + LLM call, the Japanese system prompt + JSON schema, providers |
| `src/eval.py`, `src/metrics.py`, `src/compare.py` | eval runner (`--repeats`, `--aggregate`), pure metric functions, run-vs-run diff |
| `src/pricing.py` | token prices + `estimate_cost_usd` (eval and API) |
| `src/app.py` | FastAPI `POST /chat`, `GET /health`; calls `answer_question` with no feature kwargs so it always matches eval defaults (a test enforces this). JSON-line request logs on stdout, no IPs |
| `src/limits.py`, `src/usage.py`, `src/redact.py` | per-IP rate limiter (in memory → **uvicorn must run with 1 worker**); daily question cap + token budget in Postgres `usage_daily` (JST day); phone/email redaction before logging |
| `src/static/`, `data/<tenant>/site.json` | web chat page; `site.json` holds `clinic_name` + `doc_labels` (a test requires a label for every doc, so add one when adding a doc). `?demo=` auto-send only with `DEMO_AUTOSEND=true` |
| `Dockerfile`, `docker-compose.yml` | CPU torch + bge-m3 baked in. `docker compose up -d` starts only the dev db; `--profile app` adds the API, which runs `ingest --if-empty` on start |
| `data/<tenant>/docs/` | clinic docs (source of truth for answers) |
| `data/<tenant>/doc_meta.json`, `src/doc_meta.py` | per-doc/section `doc_type` (official, news, blog, campaign) + published/effective/valid_until dates. Always used by the guardrail on the server side. Shown to the LLM only with `--doc-meta` (off by default). Keep it in sync when docs change; a test checks coverage |
| `eval/questions.jsonl` | the 50 original questions (q01–q50) |
| `eval/questions_m2_heldout.jsonl` | the 20 held-out questions (q51–q70, `split: heldout_m2`, includes the `distractor` category) |
| `eval/runs/` | committed run outputs: `<ts>_<name>.jsonl` + `.summary.json` (+ `.compare.md`); repeated runs are `<name>-r1..rN` plus `<ts>_<name>.repeats.json` |
| `tests/` | unit tests; no DB, API or model needed |
| `notes/` | data-drop notes and other reference material |

### Guardrail (unstated negatives)
- **What it targets:** a sentence claiming something is unavailable now. That means 現在/現時点/今は/もう plus ございません / しておりません / いただけません, or an offering negative such as 〜のご用意はございません / 取り扱いはございません / 扱っておりません.
- **When a cited doc can back the claim:** it must speak about the present (official, or a news/campaign section within `valid_until`) **and** contain the claim's subject. The subject is the topic noun, e.g. ギフト券, 紹介割引.
- **What happens otherwise:** the sentence is removed, a handoff sentence is added if missing, and `answer` becomes `handoff`. Each firing is logged in the row's `guardrail` field and in summaries and aggregates.
- **What it ignores:** "not in our documents" sentences (資料/記載), and restating that a dated campaign ended (終了…実施しておりません).
- **How it was calibrated:** on all 980 labeled M2 answers. Before changing it, replay it offline over the labeled runs and compare its firings with the hand labels.

## Commands
```
python -m src.ingest --tenant komorebi-seitai-demo [--if-empty]
uvicorn src.app:app --port 8000 --workers 1 --no-access-log        # API; APP_TODAY=2026-10-24 to pin the date
docker compose --profile app up --build                            # db + API in Docker
python -m src.eval --name <name> [--questions eval/questions_m2_heldout.jsonl] [--repeats 3] [--retrieval dense|hybrid|rerank] [--doc-meta] [--no-guardrail] [--no-small-docs] [--limit 5]
python -m src.eval --resummarize eval/runs/<run>.jsonl
python -m src.eval --aggregate eval/runs/<ts>_<name>.repeats.json   # after labeling repeats; no API calls
python -m src.compare eval/runs/<base>.jsonl eval/runs/<new>.jsonl [--carry-labels]
pytest
```

## Eval workflow
1. **Commit, then run** with `--repeats 3` on **both** question files for anything you want to cite. Held-out behavior varies by up to 15 points between identical runs, so report mean/min/max and the flip table. Summaries record `questions_file`, git commit/dirty, the prompt hash and feature flags.
2. **Carry labels:** run `src.compare <labeled run> <new run> --carry-labels` from each previously labeled run of the same question set, putting the most relevant base last so its `.compare.md` is the one kept. This copies `correct` only onto byte-identical answers.
3. **Review:** list only the new answers that **differ in meaning** from labeled answers.
   - Matching key facts (numbers/prices, behavior, handoff wording, unavailability claims) is a first filter. Read every unmatched answer, and always read symptom-question answers.
   - Flag anything that got worse.
4. **Hand-label:** the user labels the listed answers.
5. **Aggregate and commit:** run `--resummarize` / `--aggregate`, then commit the runs.
6. **Compare** with `src.compare` for metric deltas, behavior flips and side-by-side changed answers.

## Labeling rules
- **Hand labels come from the user.** Never decide `correct` yourself.
  - Apply labels only when the user has given or explicitly approved them. Example: "label everything else correct" after reviewing your list.
  - Labels carried by `--carry-labels` from byte-identical answers are fine.
- `correct` judges the answer content against `expected_answer` and `trap`. A content-correct answer with an off behavior label (e.g. `handoff` instead of `answer`) is still `correct: true`; behavior accuracy tracks that separately.
- `acceptable_behaviors` (optional, per question) lists the behaviors that count as correct. q54 and q64 accept `answer` or `handoff`. It's refreshed from the question file on resummarize and compare, like `gold_chunks`.
- Patterns the user has labeled **incorrect**:
  - asserting an offering doesn't exist now based only on expired or blog content (「紹介割引はございません」, 「ギフト券のご用意はございません」)
  - presenting stale blog prices or availability as current
  - handing off on q65 (towels) when the docs say no special items are needed
  - an incomplete q35 total
  - offering staff follow-up on whether treatment will cure something
- Patterns labeled **correct:** restating that a dated campaign has ended; a terse but correct handoff; extra correct facts.

## Rules
- **Commit before any run you want to cite**, so that `git_dirty` is false (`eval/runs/` is excluded from the dirty check). Don't touch tracked or untracked files outside `eval/runs/` while a run is in progress.
- **Don't resummarize old runs against new labels.** For example, runs from before the M2 FAQ split retrieved `06_faq#よくある質問`, which no longer exists. Cite their stored summaries instead; rescore in memory if a comparison needs it.
- Gold labels: a question's `gold_chunks` must exist in the corpus and fall within its `sources` (enforced by tests). Ask before changing labels that a data note doesn't specify.
- **One change per measured step:** a separate commit and a 3-repeat run for each change. Features start opt-in and become default in their own commit once results hold.
- The eval pins "today" to 2026-10-24 (`--today`) because the docs' notices are relative to it.
