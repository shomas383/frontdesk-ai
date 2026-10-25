# frontdesk-ai

A Japanese AI front desk for small clinics (整体院・鍼灸院). It answers customer questions from the clinic's own documents with citations. When the answer isn't in the documents it hands off to staff, and it never gives medical advice.

**Status: Milestones 1 and 2 complete.** Dense retrieval (bge-m3 + pgvector, top-5, with short docs expanded in full) → Claude with structured JSON output → a post-generation guardrail → a 70-question eval (50 original + 20 held-out).

## How it works

```
data/<tenant>/docs/*.md ──ingest──▶ chunks (one per ## section) ──bge-m3──▶ Postgres/pgvector
question ──bge-m3──▶ top-5 cosine (+ full doc for short docs) ──▶ Claude (JSON: answer, behavior, sources)
         ──▶ guardrail (unsupported "currently unavailable" claims → handoff) ──▶ reply / eval metrics
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
| [src/retrieval.py](src/retrieval.py), [src/lexical.py](src/lexical.py), [src/rerank.py](src/rerank.py) | Retrieval modes (`dense`, `hybrid`: dense + BM25 via RRF, `rerank`: cross-encoder over the hybrid top 20); SudachiPy tokenizer + BM25; reranker |
| [src/prompts.py](src/prompts.py) | Japanese system prompt and output JSON schema |
| `data/<tenant>/retrieval.json` | Small docs (on by default; `--no-small-docs`): if any chunk of a listed short doc (e.g. the price list) is retrieved, the LLM gets the whole doc as one block; reported as context recall |
| [src/guardrails.py](src/guardrails.py) | Guardrail (on by default; `--no-guardrail`): removes unsupported "currently unavailable" claims (backed only by blog or expired content) and hands off; each firing is logged in the run row |
| [src/doc_meta.py](src/doc_meta.py) | Loads `data/<tenant>/doc_meta.json` (doc_type `official`/`news`/`blog`/`campaign`, published/effective/valid_until dates) and shows it in each chunk's context header when `--doc-meta` is passed (off by default). It is not embedded, so retrieval is unaffected |
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
#    Held-out set (reported per category and per `split`):
python -m src.eval --name heldout --questions eval/questions_m2_heldout.jsonl
#    Retrieval mode (default dense): hybrid = dense + Japanese BM25 (SudachiPy) fused by RRF;
#    rerank = hybrid top-20 reordered by BAAI/bge-reranker-v2-m3 (local, ~2.3 GB, ~8 s/question on CPU)
python -m src.eval --name hybrid --retrieval hybrid
#    Repeat N times (mean/min/max + flip table in <ts>_<name>.repeats.json); recompute after labeling:
python -m src.eval --name v2 --repeats 3
python -m src.eval --aggregate eval\runs\<ts>_v2.repeats.json

# Unit tests (no DB, API or model needed)
pytest
```

The eval writes two files to `eval/runs/` (they are committed, so runs can be compared over time):

- `<YYYYMMDD-HHMMSS>_<name>.jsonl`: one row per question. It has the retrieved chunk_ids, answer, behavior, sources, stop_reason, latency_ms, input/output tokens, forbidden-phrase hits, the expected values for comparison, and `correct: null`.
- `<...>.summary.json`: the metrics plus the run config (model, top-k, eval date, system-prompt hash, git commit and dirty flag).

It prints:

- **Doc-level recall@5 / hit@5**: over questions with non-empty `sources`, the mean fraction of expected docs found in the top-5 chunks, and whether at least one was found.
- **Section-level recall@1/3/5, hit@5, MRR**: the same idea against `gold_chunks`, the exact `<doc_id>#<section>` chunks needed for a full answer. This is stricter, because the right doc can be retrieved without the right section, as in q35. The report also lists every question whose top 5 is missing a gold chunk.
- **behavior accuracy**: predicted `behavior` vs `expected_behavior` (or any of the question's optional `acceptable_behaviors`), overall and per category.
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

`summary.json` records `git_commit` and `git_dirty`, which is true when there are uncommitted code changes; files under `eval/runs/` are ignored. Commit before an eval run you want to cite, so that `git_dirty` is `false`.

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

## Milestone 1 — Baseline RAG pipeline and evaluation harness

**Goal:** build the simplest end-to-end system plus a reliable way to measure it, so every later change is a measured before/after instead of a guess.

### What was built

- **Pipeline:** Markdown docs → `##`-section chunks (with the doc preamble merged into each chunk) → `BAAI/bge-m3` embeddings → Postgres + pgvector → top-5 dense retrieval → Claude Haiku 5.5 → structured JSON `{answer, behavior, sources}`.
- **Behaviors:** answer only from the clinic's documents with citations; **hand off** to staff when the answer isn't in the documents; **refuse and redirect** symptom/medical questions (no diagnosis, no promised results).
- **Provider interface:** Anthropic API now, Amazon Bedrock behind the same interface (`LLM_PROVIDER`).
- **Eval harness** (`src/eval.py`, `src/metrics.py`, `src/compare.py`):
  - 50 questions across 5 categories: direct, paraphrase, multi_doc, out_of_scope, symptom_refuse
  - doc-level and section-level retrieval metrics (recall@1/3/5, hit@5, MRR)
  - behavior accuracy, hand-label accuracy, a negation-aware forbidden-phrase check
  - latency, tokens and cost
  - each run records its git commit, dirty flag and system-prompt hash; `compare` shows metric deltas and changed answers side by side
- **23 tests**; a full 50-question run costs about **$0.015**.

### Results

| Run | Change | Behavior acc. | Hand-label acc. |
|---|---|---|---|
| [`20261024-184900_baseline`](eval/runs/20261024-184900_baseline.jsonl) | — | 98% | **92%** |
| [`20261024-215215_fix-v1`](eval/runs/20261024-215215_fix-v1.jsonl) | Rule against inferring "not offered" from missing info; doc preamble merged into every chunk | 100% | **96%** |
| [`20261024-222718_fix-v2`](eval/runs/20261024-222718_fix-v2.jsonl) | Japanese-only replies; language-support questions hand off | 100% | **100%** |

Retrieval (unchanged across runs): doc recall@5 100%, **section recall@1 65.4%**, recall@3 95.7%, recall@5 97.9%, MRR 0.859.

### Failures found and fixed

| Question | Failure | Root cause | Fix |
|---|---|---|---|
| q39 (home visits), q37 (English support) | Stated "we don't offer that" though the docs never say so | Model inferred a negative fact from missing information | Prompt rule: never assert something isn't offered unless the docs say so; hand off instead |
| q27 (cheapest course) | Claimed tax inclusion wasn't stated | The "all prices include tax" note sat in a separate preamble chunk that wasn't retrieved | Merge each doc's preamble into every chunk of that doc |
| q37 (English support) | Replied in English, saying it couldn't reply in English | No fixed reply language | Always reply in Japanese (です/ます); language-support questions hand off |
| q35 (first-visit total) | Incomplete total | Gold section `01_menu_prices#初検料` not retrieved | Answer now correct via the price-revision table; retrieval miss kept as a Milestone 2 target |

### What I learned

- **Automatic metrics can hide real errors.** Behavior accuracy was 98% at baseline, but hand-labeling the answers showed 92%; the behavior metric caught 1 of 4 errors. Answer-level grading is essential, so an automatic grader checked against these hand labels is planned.
- **The most dangerous error is a confident guess.** Turning "not in the documents" into "we don't offer that" sounds authoritative and is wrong. It needs explicit rules and dedicated handoff tests.
- **Chunking decisions change answers.** Losing a document-level note ("税込") from a chunk produced a factually wrong claim.
- **Coarse metrics hide problems.** Doc-level recall@5 was 100%, but section-level recall@1 was only 65.4%. The right document was found, but often not the right section.
- **100% on a tuned test set isn't the finish line.** Each fix targeted failures already seen, so the next milestone adds new, harder, held-out questions before optimizing retrieval.

## Milestone 2 — Retrieval quality on a harder benchmark

**Goal:** make the benchmark harder, then improve retrieval and measure every change honestly, including on questions the earlier fixes were never tuned on.

### What changed in the benchmark

- **Noise documents:** a 2025 blog post with an old price and stale availability, expired 2025 campaigns, a staff page, and a long column. The FAQ is split into one chunk per Q&A, giving 51 chunks.
- **20 held-out questions (q51–q70):** a new `distractor` category (old prices, expired campaigns, past notices) plus paraphrase, multi-doc, out-of-scope and symptom questions.
- **Eval tooling:**
  - `--questions` and per-split reporting
  - `--repeats N` with mean/min/max and a flip table, recomputed after labeling with `--aggregate`
  - `acceptable_behaviors`: q54 and q64 accept either `answer` or `handoff`
  - context recall: the share of gold sections the LLM actually saw

### Results

The original 50 questions and the 20 held-out questions are shown side by side, as original / held-out. Rows with 3 repeats show the mean. Behavior is scored with `acceptable_behaviors` in every row. Prompt-only rows keep the baseline retrieval.

| Run | Change | Repeats | Hand-label | Behavior | Section r@1 | MRR | Context recall | Latency |
|---|---|---|---|---|---|---|---|---|
| `m2-baseline` | M1 system on the harder data (dense, top-5) | 1 | 98% / 85% | 100% / 95% | 61.3% / 58.8% | 0.827 / 0.821 | 97.9% / 97.1% | 1.5 s |
| `m2-hybrid` | + Japanese BM25 (SudachiPy), RRF fusion | 1 | 96% / 85% | 100% / 90% | 65.0% / 64.7% | 0.848 / 0.838 | 94.2% / 94.1% | 1.6 s |
| `m2-rerank` | + bge-reranker-v2-m3 over the fused top 20 | 1 | 98% / **75%** | 100% / 85% | **69.6% / 70.6%** | **0.883 / 0.853** | 94.2% / 85.3% | **9.0 s** |
| `m2-stale-rule` | dense; prompt: blog, expired campaigns and past notices are never evidence of what's current | 1 | 98% / 100% | 98% / 100% | = baseline | = baseline | 97.9% / 97.1% | 1.4 s |
| `m2-authority` | + doc_type/date metadata in the context (not adopted) | 1 | 98% / 90% | 98% / 100% | = baseline | = baseline | 97.9% / 97.1% | 1.5 s |
| `m2-stale-v2` | + medical questions always `refuse_redirect`; no staff follow-up on treatment effects | 3 | 98.0% / 96.7% | 100% / 98.3% | = baseline | = baseline | 97.9% / 97.1% | 1.4 s |
| `m2-guardrail` | + post-generation guardrail for unstated negatives | 3 | 98.7% / 98.3% | 100% / 98.3% | = baseline | = baseline | 97.9% / 97.1% | 1.4 s |
| `m2-smalldocs` | + full price list in the context when any of its chunks is hit | 3 | 98.7% / 100% | 99.3% / 98.3% | = baseline | = baseline | **100% / 100%** | 1.4 s |
| `m2-guardrail-v2` | guardrail checks the claim's subject against the cited doc; **adopted as the default** | 3 | **100% / 100%** | 100% / 96.7% | = baseline | = baseline | **100% / 100%** | 1.5 s |

- **Cost:** a full run costs about $0.017 (original set) and $0.007 (held-out).
- **Run files:** in [eval/runs/](eval/runs/). Each 3-repeat row also has a `.repeats.json` aggregate.
- **Final configuration:** 100% hand-label accuracy on both sets in all 3 repeats. The held-out behavior misses (q52, q65 in r2) are content-correct answers labeled `handoff`.

### Failures found and fixed

| Question | Failure | Root cause | Fix |
|---|---|---|---|
| q55, q36, q56 | 「現在は紹介割引はございません」 or 「ギフト券のご用意はございません」 | The model turned a 2025 campaign that ended into a claim about today | Stale-content prompt rule, then a guardrail that removes "currently unavailable" claims no current, official source backs |
| q36 (with small docs) | Same claim, now citing the price list | The full price list was in context, and it never mentions gift tickets | The guardrail only accepts an official doc if the claim's subject actually appears in it |
| q64, q53 | Risk of repeating the blog's 「土曜午前が空いている」 or 6,600円 as current | Old blog text was retrieved | Stale-content rule; official docs override blog and news |
| q49, q50 | Pregnancy question labeled `handoff`; "staff will confirm whether it cures" | Rule conflict between handoff and medical | Medical questions take precedence and are always `refuse_redirect`; no staff follow-up on effects |
| q35 | Incomplete first-visit total (an open retrieval miss since M1) | The `初検料` section was not in the top 5 | The short price list is shown in full whenever any of its chunks is retrieved: complete in 3 of 3 repeats, versus 1 of 3 |
| q52 (rerank) | 「水曜日は当院の定休日ではなく…」 | The reranker dropped the `定休日` chunk | Not adopted: dense stays the default |

### What I learned

- **Better ranking didn't mean better answers.** The reranker raised section recall@1 by 8–12 points and MRR by up to 0.056. But recall@5 fell, it introduced a confident factual error (q52), and held-out hand-label accuracy fell to 75%, all at six times the latency.
- **The noise documents broke reasoning, not retrieval.** The right chunks were already retrieved; the model treated expired or blog content as current. A prompt rule and a narrow, deterministic guardrail fixed that, and retrieval changes didn't.
- **Single runs are noise.** Identical runs varied by up to 15 points in held-out behavior accuracy, so every adopted change is measured over 3 repeats with a flip table.
- **What the LLM sees matters more than rank.** Showing the whole price list fixed a retrieval miss that ranking never did. It also gave the model a document to over-generalize from, and the guardrail's subject check catches that.
- **Metadata didn't beat a plain rule.** Adding document type and dates to the context cost about 10% more tokens and didn't improve on the stale-content rule.
- **Calibrating a guardrail on hand labels pays off.** Replayed over all 980 labeled M2 answers, the final guardrail fires on exactly the answers labeled wrong for this failure, and never on an answer labeled correct.

### Next

See the [Roadmap](#roadmap). The automatic grader (M3) is deferred so the public demo (M4) can come first.

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| M1 — Baseline RAG pipeline and evaluation harness | Dense RAG, structured output, 50-question eval, hand labels | Complete (tag `milestone-1`) |
| M2 — Retrieval quality on a harder benchmark | Noise docs, held-out set, hybrid/rerank experiments, stale-content rule, guardrail, small-docs expansion | Complete (tag `milestone-2`) |
| M3 — Automatic answer grader | An LLM grader calibrated against the M1/M2 hand labels | Planned, deferred |
| M4 — Public demo | Web chat, LINE integration, AWS deployment (Bedrock), demo video | In progress |

Hybrid search and reranking were tried in M2. They remain available as `--retrieval hybrid|rerank` but are not the default.
