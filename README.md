# frontdesk-ai

A Japanese AI front desk for small clinics (整体院・鍼灸院). It answers customer questions from the clinic's own documents with citations. When the answer isn't in the documents it hands off to staff, and it never gives medical advice.

**Status: Milestone 1, baseline.** Dense retrieval (bge-m3 + pgvector, top-5) → Claude with structured JSON output → a 50-question eval.

## How it works

```
data/<tenant>/docs/*.md ──ingest──▶ chunks (one per ## section) ──bge-m3──▶ Postgres/pgvector
question ──bge-m3──▶ top-5 cosine ──▶ Claude (JSON: answer, behavior, sources) ──▶ eval metrics
```

- **Multi-tenant ready.** Each clinic is a folder under `data/<tenant>/`, and every chunk row stores `tenant_id`.
- **Behaviors:**
  - `answer`: the answer is in the docs, with doc_ids cited.
  - `handoff`: the answer isn't in the docs, so staff will check.
  - `refuse_redirect`: a symptom or medical question. No judgement, no "治る"; the customer is pointed to a medical institution or a consultation.

| File | Purpose |
|---|---|
| [src/chunking.py](src/chunking.py) | Splits Markdown by `##` headings. `chunk_id = <doc_id>#<section>` |
| [src/ingest.py](src/ingest.py) | Embeds and upserts a tenant's chunks. Re-runnable and removes stale chunks |
| [src/answer.py](src/answer.py) | Retrieval + LLM call → `AnswerResult` |
| [src/prompts.py](src/prompts.py) | Japanese system prompt and output JSON schema |
| [src/llm.py](src/llm.py) | Provider interface (`LLM_PROVIDER=anthropic` or `bedrock`) |
| [src/eval.py](src/eval.py), [src/metrics.py](src/metrics.py) | Eval runner and pure metric functions |

## Setup (Windows / PowerShell)

Prerequisites: Python 3.11+ (conda shown here; a plain `python -m venv .venv` works too) and Docker Desktop.

```powershell
conda create -n frontdesk-ai python=3.12 -y
conda activate frontdesk-ai
pip install -r requirements.txt

copy .env.example .env      # then set ANTHROPIC_API_KEY in .env
docker compose up -d        # Postgres 17 + pgvector on localhost:5433
```

The first ingest downloads `BAAI/bge-m3` (about 2.3 GB) into the Hugging Face cache. It runs on CPU.

## Run: ingest → answer → eval

```powershell
# 1. Chunk + embed + upsert the demo clinic's docs (safe to re-run)
python -m src.ingest --tenant komorebi-seitai-demo

# 2. Ask one question (prints the JSON result incl. retrieved chunks and tokens)
python -m src.answer "60分の全身整体はいくらですか？"

# 3. Run the eval set (use --limit 5 for a quick smoke run)
python -m src.eval --name baseline

# Unit tests (no DB, API or model needed)
pytest
```

The eval writes two files to `eval/runs/` (they are committed, so runs can be compared over time):

- `<YYYYMMDD-HHMMSS>_<name>.jsonl`: one row per question. It has the retrieved chunk_ids, answer, behavior, sources, stop_reason, latency_ms, input/output tokens, forbidden-phrase hits, the expected values for comparison, and `correct: null`.
- `<...>.summary.json`: the metrics plus the run config (model, top-k, eval date, system-prompt hash, git commit and dirty flag).

It prints:

- **Doc-level recall@5 / hit@5**: over questions with non-empty `sources`, the mean fraction of expected docs found in the top-5 chunks, and whether at least one was found.
- **Section-level recall@1/3/5, hit@5, MRR**: the same idea against `gold_chunks`, the exact `<doc_id>#<section>` chunks needed for a full answer. This is stricter, because the right doc can be retrieved without the right section, as in q35. The report also lists every question whose top 5 is missing a gold chunk.
- **behavior accuracy**: predicted `behavior` vs `expected_behavior`, overall and per category.
- **forbidden phrases**: a rule-based check for 治る/治ります/効く/改善します and similar. The headline number is the violation rate on `refuse_redirect` questions.
  - A match is skipped when its clause ends in a polite negation (〜ません/〜かねます), so refusals like 「治る時期や回数をお答えすることはできません」 aren't flagged.
  - The clause stops at 。/が、/ので, so 「治りますので、予約はできません」 is still flagged.
- stop_reason counts, errors, mean and p95 latency, tokens, and estimated cost.

The eval pins "today" to `2026-10-24` (override with `--today`), because the dataset's notices are relative to that date. The answer CLI uses the real date.

### Hand-labeling answers

Behavior accuracy is automatic, but answer correctness is not. To label a run:

1. Open the run's `.jsonl` and set `"correct": true` or `false` per row. An optional `"label_note"` can explain a miss.
2. Compare against `expected_answer` and the `trap` field, which describes a common wrong answer.
3. Recompute the summary, which adds hand-label accuracy overall and per category. This makes no API calls.

```powershell
python -m src.eval --resummarize eval\runs\<run>.jsonl
```

### Comparing runs

```powershell
python -m src.compare eval\runs\<base>.jsonl eval\runs\<new>.jsonl [--carry-labels]
```

- Prints metric deltas, overall and per category, and the list of behavior flips.
- Writes `<new>.compare.md`, which shows every changed answer side by side with the base answer and its label.
- `--carry-labels` copies `correct`/`label_note` from base to new for answers that are byte-identical, so only the changed answers need labeling.

### Reproducibility

`summary.json` records `git_commit`, which needs a git repo. Run `git init` and commit before an eval run you want to cite; until then the field is `null`.

## Configuration (.env)

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `bedrock` |
| `LLM_MODEL` | `claude-haiku-5-5` | Anthropic API model ID |
| `ANTHROPIC_API_KEY` | none | required for `anthropic` |
| `AWS_REGION` / `BEDROCK_MODEL_ID` | `us-east-2` / none | for `bedrock`; use a `us.` inference-profile ID and standard AWS credentials |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5433/frontdesk` | matches `docker-compose.yml` (host port 5433) |
| `EMBED_MODEL` | `BAAI/bge-m3` | normalized, 1024 dims |

Cost: the model runs with thinking disabled, `effort: low` and `max_tokens: 500`. A full 50-question run on Haiku 5.5 costs well under $0.10.

## Roadmap

Later milestones: FAQ-aware chunking, hybrid (keyword + dense) search, reranking, LINE integration, a web UI, and AWS deployment (Bedrock).
