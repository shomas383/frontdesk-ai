# frontdesk-ai

A Japanese AI front desk for small clinics (整体院・鍼灸院). It answers customer questions from the clinic's own documents with citations. When the answer isn't in the documents it hands off to staff, and it never gives medical advice.

**Status: Milestones 1 and 2 complete; M4 (public demo) in progress — API, web chat and AWS deploy done; live at https://demo.shomasawa.com ([Deploy](#deploy-aws)).** Dense retrieval (bge-m3 + pgvector, top-5, with short docs expanded in full) → Claude with structured JSON output → a post-generation guardrail → a 70-question eval (50 original + 20 held-out).

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
| [src/app.py](src/app.py), [src/limits.py](src/limits.py), [src/usage.py](src/usage.py), [src/redact.py](src/redact.py) | HTTP API (see [API](#api-local)); per-IP rate limiter; daily cap/token budget in Postgres; phone/email redaction for logs |
| [deploy/](deploy/) | Production compose file (Caddy + app + pgvector), Caddyfile, `deploy.sh`, `smoke.py`; shipped inside the image |
| [infra/](infra/) | Terraform for the EC2 host and DNS record, plus the host's pull-based deploy ([infra/README.md](infra/README.md)) |
| [.github/workflows/](.github/workflows/) | `test.yml` (pytest + gitleaks on PRs and `main`), `deploy.yml` (a `v*` tag publishes a release) |

## Setup (Windows / PowerShell)

Prerequisites: Python 3.11+ (conda shown here; a plain `python -m venv .venv` works too) and Docker Desktop.

```powershell
conda create -n frontdesk-ai python=3.12 -y
conda activate frontdesk-ai
pip install -r requirements.txt

copy .env.example .env      # then set ANTHROPIC_API_KEY in .env
docker compose up -d        # Postgres 17 + pgvector on localhost:5433 (the API service is opt-in, see below)
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
| `ALLOWED_TENANTS` | `komorebi-seitai-demo` | API: comma-separated tenants `/chat` accepts |
| `MAX_MESSAGE_CHARS` | `300` | API: longer messages get 422 |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_PER_DAY` | `10` / `50` | API: per client IP, in memory |
| `DAILY_QUESTION_CAP` / `DAILY_TOKEN_BUDGET` | `500` / `2000000` | API: global per JST day, in Postgres; then a polite handoff |
| `APP_TODAY` | today in Asia/Tokyo | API: pin the docs' reference date (e.g. `2026-10-24`, as in eval) |
| `TRUST_PROXY_HEADERS` | `false` | API: rate-limit by `X-Forwarded-For` (only behind a trusted proxy) |
| `CORS_ORIGINS` | none | API: comma-separated browser origins |
| `APP_PORT` | `8000` | host port for `docker compose --profile app up` |
| `DEMO_AUTOSEND` | `false` | web page: allow `?demo=q1\|q2` auto-send (screenshots / demo video) |

Cost: the model runs with thinking disabled, `effort: low` and `max_tokens: 500`. A full 50-question run on Haiku 5.5 costs well under $0.10.

## API (local)

`src/app.py` serves a web chat page at `/` and the same answer path as the eval: dense top-5, small-docs expansion, prompt v2 and the guardrail, with the same defaults as `python -m src.eval`.

```powershell
# Dev: against the database on localhost:5433 (APP_TODAY pins the date the docs are judged against)
$env:APP_TODAY = "2026-10-24"
uvicorn src.app:app --port 8000 --workers 1 --no-access-log

# Docker: Postgres + API; ingests ALLOWED_TENANTS on first start (skipped once chunks exist)
docker compose --profile app up --build
```

```bash
curl -s localhost:8000/health
curl -s localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"tenant": "komorebi-seitai-demo", "message": "60分の全身整体はいくらですか？"}'
# {"answer": "全身整体60分の料金は7,150円（税込）です。…", "behavior": "answer", "sources": ["01_menu_prices", "07_news"]}
```

| Endpoint | Response |
|---|---|
| `POST /chat` `{tenant, message}` | `200 {answer, behavior, sources}`. If the pipeline fails, a staff-handoff answer |
| | `404` unknown tenant · `422` empty message or over `MAX_MESSAGE_CHARS` · `429` per-IP limit (with `Retry-After`) |
| | Daily question cap or token budget used up: `200` with 「…お電話またはLINEにて当院へ直接お問い合わせください。」, `behavior: handoff`; the LLM is not called |
| `GET /health` | `200 {status, db, model, version, git_sha}` (`version`/`git_sha` come from the image build args; `dev`/`unknown` locally), or `503` if the database is unreachable |
| `GET /` | The web chat (below) |
| `GET /api/config` | `{tenant, clinic_name, doc_labels, max_message_chars, demo_autosend}` for the page |

**Protection:**
- Per-IP limits (`RATE_LIMIT_PER_MINUTE`/`_PER_DAY`) live in process memory, keyed by a salted hash of the IP.
- The global per-day limits (`DAILY_QUESTION_CAP`, `DAILY_TOKEN_BUDGET`, by JST date) are in the Postgres table `usage_daily`. Restarts don't reset them.
- The token budget can be overshot by the requests already in flight, about 3k tokens each.

**Single worker only.** Run uvicorn with `--workers 1`, as the Dockerfile does. With N workers each process would keep its own rate-limit counts, and a client would get N× the limit. Move the limiter to a shared store before scaling out.

**Footprint** (measured 2026-10-25 with `docker stats`):
- **App:** about 0.8 GB resident while serving, with bge-m3 loaded and ~1.6 s per question. The peak is about 1.1 GB, while embedding all chunks during the first-start ingest.
- **Postgres:** about 75 MB.
- **Image:** 3.3 GB, with CPU-only torch and bge-m3 baked in.
- **Sizing:** the demo needs a 4 GB instance. Resident memory alone would fit in 2 GB, but bge-m3's weights (~1.1 GB) are memory-mapped and also need room in the page cache (see [Resize to t3.medium](#resize-to-t3medium-2026-10-27)).

### Web chat

Open http://localhost:8000/. It is plain HTML/CSS/JS in [src/static/](src/static/): no build step, no CDNs, no web fonts.

- **Header:** a disclaimer banner and an "About this demo" link to this repo.
- **Suggested-question chips:** they show off the strengths: price, holiday hours, a stale blog price, an ended campaign, and a medical question.
- **Under each answer:** source chips with friendly names from `data/<tenant>/site.json`, and a tag for `handoff` (スタッフにおつなぎします) or `refuse_redirect` (医療機関へのご相談をおすすめ).
- **Input and errors:** a 300-character counter, and friendly messages for 422, 429 (with a live `Retry-After` countdown) and network errors.
- **Nothing is stored:** no localStorage or cookies; a reload clears the chat. Answers are inserted as text, never as HTML.
- **Security headers:** a strict CSP (`default-src 'self'`), `nosniff`, and `no-referrer`.
- **`?demo=q1|q2`** auto-sends questions for screenshots and the demo video. It works only with `DEMO_AUTOSEND=true` (off by default).

### Logs and retention

Each request is logged as one JSON line on stdout:
- `question`, `tenant`, `outcome` (`answered`/`rate_limited`/`cap`/`budget`/`error`/`invalid`)
- `behavior`, `sources`, `guardrail_fired`, `guardrail_removed`
- `latency_ms`, `llm_latency_ms`, `input_tokens`, `output_tokens`, `cost_usd`, `error`

**Privacy:**
- **Not logged:** IP addresses, user agents and session IDs. Uvicorn's access log is disabled because it prints client IPs.
- Phone numbers and email addresses in the question are replaced with `[電話番号]` / `[メール]` before logging (`src/redact.py`). The LLM still sees the original message.
- Rejected requests (rate-limited, invalid) don't log the question text.

**Retention:**
- **Locally:** logs exist only in the container's Docker log (`docker compose logs app`) and are deleted with the container.
- **On AWS:** every container (app, Postgres, Caddy) logs to the CloudWatch log group `/frontdesk-ai/prod` through Docker's awslogs driver. The group has a fixed retention of 30 days (`infra/logs.tf`). Usage counters in `usage_daily` hold only per-day totals.
- **Caddy** has no access log. Its error log goes through a filter that deletes the client address and request headers. For example, a 502 during a redeploy logs the method, host and URI, but no IP.

## Deploy (AWS)

The demo runs at **https://demo.shomasawa.com** on one EC2 instance, a t3.medium in us-east-2 with 4 GB RAM. The instance runs `deploy/compose.prod.yml`: Caddy for HTTPS, then the app, then Postgres with pgvector. Terraform in [infra/](infra/) defines the AWS side. A git tag starts a release.

```mermaid
flowchart LR
  visitor([Browser]) -- "DNS: Cloudflare, DNS only" --> eip[Elastic IP]
  subgraph ec2["EC2 t3.medium · Amazon Linux 2023 · SG: 80/443 only"]
    caddy["Caddy<br/>Let's Encrypt, HTTP→HTTPS"] --> app["app<br/>FastAPI + bge-m3, 1 worker"] --> db[("Postgres + pgvector")]
    timer["frontdesk-deploy.timer<br/>every 2 min"] -. "new :prod digest → deploy.sh" .-> app
  end
  eip --> caddy
  app -- "/chat" --> claude[Anthropic API<br/>Haiku 5.5]
  timer -- "pull :prod" --> ghcr[(ghcr.io/shomas383/frontdesk-ai)]
  ec2 -- "awslogs" --> cw[CloudWatch Logs<br/>30 days]
  ec2 -- "secrets at deploy" --> ssm[Parameter Store<br/>/frontdesk-ai/prod/*]
  gha[GitHub Actions<br/>tag v*] -- "build, push :tag, move :prod" --> ghcr
  gha -. "smoke test" .-> caddy
```

**Why it's built this way:**
- **One small box.** The app needs about 0.8 GB, rising to about 1.1 GB during ingest, and Postgres about 75 MB. The memory-mapped model weights (~1.1 GB) must also stay in the page cache, so the box is 4 GB, with a 2 GB swap file as a safety net. A load balancer, NAT gateway or managed database would cost more than the whole instance.
- **The deploy is pull-based.** This account belongs to an AWS Settings project. Its fixed SCP denies `iam:*Provider*`, so GitHub Actions can't use OIDC to reach AWS, and long-lived AWS keys in GitHub are ruled out. Instead, GitHub only publishes the image, and the host deploys itself. GitHub holds no AWS credentials of any kind.
- **DNS only (grey cloud).** Visitors connect straight to Caddy, which gets its own certificate. The app sees the real client IP through `X-Forwarded-For` (`TRUST_PROXY_HEADERS=true`) for the per-IP limit. Caddy overwrites any client-sent value, and the app port isn't published, so the header can't be spoofed.
- **No SSH.** The security group opens only ports 80 and 443. Shell access goes through SSM Session Manager.
- **Secrets stay out of git, the image and Terraform state.** `ANTHROPIC_API_KEY` and `POSTGRES_PASSWORD` are SecureString parameters, which `deploy.sh` reads at deploy time into a root-only `.env` file. The instance metadata hop limit is 1, so containers can't use the instance role.

**Production settings** (`deploy/compose.prod.yml`, checked by `tests/test_deploy_files.py`):
- `APP_TODAY=2026-10-24`, so answers match the labeled eval runs.
- `DEMO_AUTOSEND=false`, and no CORS.
- One uvicorn worker, with no access log.
- A full ingest on every start, so the database always matches the docs baked into the image.
- The Step 1 limits: 10/min and 50/day per IP; 500 questions and 2M tokens per JST day overall. Any limit can be overridden by a parameter of the same name under `/frontdesk-ai/prod/`.

### Cost

| | $/month (us-east-2 on-demand) |
|---|---|
| EC2 t3.medium, standard CPU credits (no unlimited-mode surcharges) | ~30.35 |
| EBS gp3 30 GB | 2.40 |
| Public IPv4 (Elastic IP) | 3.65 |
| CloudWatch Logs, Parameter Store (standard), Session Manager, GHCR (public package), Cloudflare DNS | ~0 |
| **Total while running** | **≈ 36.50** |
| While stopped (EBS + IP) | ≈ 6.05 |

Anthropic usage is capped separately by the daily limits, at roughly $0.25/day at most. The AWS account has a $50/month budget alert and a $50 project spend limit; near that limit, AWS applies a deny SCP. Running all month leaves about $13.50 of margin under that limit. The t3.small cost ~15.20, or ≈ 21.40 in total, but was too slow (see [Resize to t3.medium](#resize-to-t3medium-2026-10-27)).

### One-time setup

1. **Parameters:** in Systems Manager → Parameter Store, create `/frontdesk-ai/prod/ANTHROPIC_API_KEY` and `/frontdesk-ai/prod/POSTGRES_PASSWORD`.
   - Both are SecureString, using the default `aws/ssm` key.
   - The password must use only letters, digits and `. _ ~ -`, because `deploy.sh` checks it.
2. **Infrastructure:** `terraform init`, then `plan`, then `apply` in `infra/`, with a Cloudflare token limited to DNS:Edit on the zone. See [infra/README.md](infra/README.md). That file also covers backing up the local state.
3. **First release:** run `git tag v0.4.0 && git push origin v0.4.0`.
   - GHCR packages pushed from a private repo start out private. After that first push, set the package to **Public** (Package settings → Change visibility); the host pulls anonymously.
   - Then re-run the failed smoke job.

### Releasing and rolling back

```bash
git tag v0.4.1 && git push origin v0.4.1
```

1. **Test:** `deploy.yml` runs the tests and gitleaks.
2. **Publish:** it builds and pushes `ghcr.io/shomas383/frontdesk-ai:v0.4.1`, with the version and git SHA baked in, then moves `:prod` to that image.
3. **Deploy:** within 2 minutes the host's timer sees a new `:prod` digest. It copies `deploy/` out of that image and runs its `deploy.sh`. The app ingests the docs, loads the model and becomes healthy.
4. **Smoke test:** the job waits until `/health` reports `v0.4.1`, then asks 3 eval questions (price → `answer`, gift card → `handoff`, symptom → `refuse_redirect`).

**If a release fails on the host**, `pull-deploy.sh` records the digest as failed, so it isn't retried every 2 minutes, and **redeploys the previous digest automatically**. The smoke job then fails, because `/health` never reports the new version.

**To roll back on purpose**, open an older tag's run of `deploy.yml` and choose **Re-run all jobs**. Its image already exists, so the build is skipped, `:prod` moves back, and the host follows. This was verified on the live site (v0.4.1 → v0.4.0 → v0.4.1).

The site is down while the app restarts on each deploy, as it ingests the docs and then loads the model. Measured on the t3.small, that's about 1.5 minutes of HTTP 502 from Caddy, plus the image pull before it. A release that changes the large layers (torch, the model) takes about 4 minutes to pull.

**Digests:** `:<tag>` is the image manifest. Until v0.4.0, `:prod` was a one-entry OCI index wrapping that manifest, created by `imagetools create`. So the host's `history.log` showed a different digest for the same image: for v0.4.0, index `sha256:287055d1…` wraps manifest `sha256:49c64a9a…`. From v0.4.1, `deploy.yml` copies the manifest as-is (`--prefer-index=false`), so the host records the tag's own digest.

**First release (v0.4.0, 2026-10-27):**

| | |
|---|---|
| Build + push, cold cache (`publish` job) | 14 min 55 s |
| Image size | ~3.26 GB |
| `:prod` moved → `/health` reports v0.4.0 | ~6 min once the GHCR package was public. The first smoke run timed out after 20 min because the package was still private; after making it public and re-running the smoke job, the stages were: ~4 min connection refused (image pull), one TLS alert (certificate being issued), ~1.5 min HTTP 502 (ingest + model load), then healthy. The smoke step took 6 min 18 s |
| Smoke test | 3/3 passed: q01 `answer` citing `01_menu_prices`, `07_news`; q36 `handoff`; q44 `refuse_redirect` |
| Memory after deploy | `free -h`: 968 Mi used of 1.9 Gi, 868 Mi buff/cache, 761 Mi available; swap 99 Mi used of 2.0 Gi. `docker stats`: app 931 MiB, db 43 MiB, caddy 18 MiB |
| Per-IP limit | 11 parallel `POST /chat` from one IP: 10× 200 and 1× 429 (`Retry-After: 51`). A person in a browser can't trigger it, since each answer takes several seconds |
| Stop / start | Back ~2.5 min after start (boot, ingest, uvicorn), with no redeploy; same Elastic IP, certificate still valid |
| Answer latency | 8–9 s (sometimes 5–7 s), compared with 1.45 s median locally (LLM 1.30 s + everything else 0.15 s, over 210 eval rows). CPU credits are ruled out: balance ~38 and rising, CPU 2–3% when idle. Root cause: the model weights were re-read from disk on every question (see [Resize to t3.medium](#resize-to-t3medium-2026-10-27)) |
| Rollback test (with v0.4.1) | Passed. The host's `history.log` digest for v0.4.1 equals the GHCR `:v0.4.1` digest (the `--prefer-index=false` fix). Re-running the v0.4.0 run brought back `/health` `v0.4.0` (`f39cef3`); re-running the v0.4.1 run restored `v0.4.1` (`0a1be25`) |

#### Resize to t3.medium (2026-10-27)

On the t3.small, almost all of the answer time was spent outside the LLM:
- The Logs Insights latency split (below), over 2 days and 31 answered questions, gave median total 10,268 ms, median LLM 1,482 ms and median everything else 8,771 ms.
- `vmstat 1` during one question showed disk reads (`bi`) of 120k–230k KB/s for about 9 s, I/O wait (`wa`) of 43–65%, and low user CPU.

The app's process memory (~0.7 GB) and bge-m3's memory-mapped `model.safetensors` (~1.1 GB read per question) didn't both fit in RAM on a 2 GB box. The kernel dropped the weights from the page cache between questions, so every embedding re-read them from EBS. That read is capped by gp3's 125 MB/s baseline: 1.1 GB ÷ 125 MB/s ≈ 9 s.

The fix is the 4 GB t3.medium (`instance_type` in `infra/variables.tf`). Terraform changes the type in place (stop, modify, start), keeping the instance, its volume and the Elastic IP.

| | t3.small (before) | t3.medium (after) |
|---|---|---|
| Latency split, median total / LLM / other | 10,268 / 1,482 / 8,771 ms (n=31) | pending |
| `vmstat` during a question | `bi` 120k–230k KB/s for ~9 s, `wa` 43–65% | pending |
| `free -h` | 968 Mi used of 1.9 Gi, 99 Mi swap | pending |

### Operating it

| Task | How |
|---|---|
| Shell on the host | `aws ssm start-session --region us-east-2 --target <instance-id>` (`terraform output session_command`) |
| App / Caddy / Postgres logs | CloudWatch → Log groups → `/frontdesk-ai/prod` (streams `frontdesk-app-1`, `-caddy-1`, `-db-1`) |
| Deploy history | on the host: `journalctl -u frontdesk-deploy`, `cat /opt/frontdesk/state/history.log` |
| Stop (≈ $6/month while stopped) | `aws ec2 stop-instances --region us-east-2 --instance-ids <id>` |
| Start | `aws ec2 start-instances --region us-east-2 --instance-ids <id>`. The IP, containers and certificates come back on their own; no redeploy |
| Turn the LLM off without stopping | create `/frontdesk-ai/prod/DAILY_QUESTION_CAP` = `0`, then on the host run `sudo /opt/frontdesk/pull-deploy.sh --force` (re-renders `.env` and restarts). `/chat` then answers with the polite "please call the clinic" handoff |
| Change a limit | the same: a parameter with the variable's name, then `pull-deploy.sh --force` |

**CloudWatch Logs Insights queries** (CloudWatch → Logs Insights → log group `/frontdesk-ai/prod`; save them with **Save** to keep them in the console).

*IP audit.* Lists every IPv4-shaped string in the logs, by stream. Expect only `0.0.0.0`, `127.0.0.1` and Docker's internal `172.16–31.x.x` addresses (e.g. the app container in Caddy's 502 lines during a deploy). From v0.4.1, Caddy also deletes the top-level `remote`, `remote_ip` and `client_ip` fields, which held the Let's Encrypt validators' addresses in its "served key authentication" lines. Any other address is a leak to fix. The query extracts only the first address in each line, so for a full audit also search for a specific one with `filter @message like "203.0.113.7"`.

```
parse @message /(?<ip>[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/
| filter ispresent(ip)
| stats count(*) as lines, earliest(@timestamp) as first_seen, latest(@timestamp) as last_seen by ip, @logStream
| sort lines desc
```

*Latency split.* Separates LLM time from everything else (embedding, DB, guardrail) for answered questions:

```
filter event = "chat" and outcome = "answered"
| fields latency_ms - llm_latency_ms as other_ms
| stats count(*) as n, pct(latency_ms, 50) as total_p50, pct(llm_latency_ms, 50) as llm_p50, pct(other_ms, 50) as other_p50, pct(latency_ms, 90) as total_p90 by bin(1d)
```

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

### Backlog

- **Unneeded staff follow-up on fully answered questions.** When the official docs fully answer a question, the bot sometimes still adds 「スタッフが確認のうえご案内」. For example, it corrects a blog's 6,600円 to the current 7,150円 and then defers to staff anyway, which makes a correct answer sound uncertain. Seen in the web-chat screenshots (2026-10-25), where `behavior` stayed `answer`. Reproduced on the live site (2026-10-27): 「平日の営業時間は？」 ended with 「詳しくはお気軽にお問い合わせください」.
  - Candidate fix: a prompt rule that tells the model not to add a handoff sentence when the cited official docs answer the question completely.
  - Check it with a 3-repeat eval on both question sets, plus a count of answer-behavior rows that contain a handoff phrase.
  - Watch that it doesn't suppress real handoffs (q36, q54, q64) or the medical redirects.
