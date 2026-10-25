# Milestone 2 — harder benchmark (data drop)

Copy the files into the repo:

| File in this zip | Destination | Action |
|---|---|---|
| `docs/06_faq.md` | `data/komorebi-seitai-demo/docs/06_faq.md` | **Replace** (FAQ split into one `##` section per Q&A) |
| `docs/08_blog_katakori.md` | same docs folder | New — blog post with an **outdated price** (6,600円, 2025) and stale availability |
| `docs/09_staff.md` | same docs folder | New — staff intro; adds LINE reply hours; mentions the director's dog |
| `docs/10_campaign_2025.md` | same docs folder | New — **expired** 2025 campaigns (回数券 27,000円, 紹介割引, 年末ギフト) |
| `docs/11_column_aftercare.md` | same docs folder | New — long column; one genuinely useful section (施術のあとの過ごし方) |
| `eval/questions_m2_heldout.jsonl` | `eval/questions_m2_heldout.jsonl` | New — 20 held-out questions (q51–q70) with `gold_chunks` and `split: "heldout_m2"` |

## Instructions for Claude Code

1. Copy the files as above, then re-ingest. The chunk count should rise from 28 to about 55.

2. **Update existing gold labels for the FAQ split.** `06_faq#よくある質問` no longer exists. In `eval/questions.jsonl`, replace it in `gold_chunks` using this mapping:

   | Questions | New gold chunk |
   |---|---|
   | q10 | `06_faq#健康保険` |
   | q12, q24 | `06_faq#着替え` |
   | q14 | `06_faq#予約なしの来院` |
   | q15 | `06_faq#子どもの施術` |
   | q16 | `06_faq#領収書` |
   | q09, q22, q29 | `06_faq#支払い方法` |
   | q23 | `06_faq#施術の痛み` |
   | q44, q45, q47 | `06_faq#強い痛み・しびれ` (q44 may also accept `06_faq#通院ペース`) |
   | q49 | `06_faq#妊娠中の施術` |

   Doc-level `sources` don't change. If any other question still references `06_faq#よくある質問`, list it for me rather than guessing.

3. **One existing question is affected by the new docs:** q36 (ギフト券) is still `handoff`, but `10_campaign_2025` now mentions a 2025-only gift ticket. Update its `expected_answer` to: "2025年の年末に限定販売したことはあるが、現在の販売については資料に記載がないため、スタッフに確認する旨を伝える." Add `trap`: "2025年限定の販売を現在も買えると答えたら不正解".

4. **Eval CLI:** add `--questions` (default `eval/questions.jsonl`) so the held-out set can be run on its own. Report metrics per file, and per `split` when present. `distractor` is a new category; make sure per-category tables handle it.

5. **Re-baseline with retrieval unchanged** (same embedding model, top_k=5, dense only):
   - `--name m2-baseline-original` on `eval/questions.jsonl` (shows what the noise docs and FAQ split did to the old set)
   - `--name m2-baseline-heldout` on `eval/questions_m2_heldout.jsonl`

   Commit before running so `git_dirty` is false. Don't change the prompt or retrieval yet.

## What the held-out set tests

| Category | n | What it checks |
|---|---|---|
| distractor | 6 | Outdated blog price, expired campaigns, a past summer-closure notice, stale "Saturday is free" availability |
| multi_doc | 4 | Discount combination rules, price arithmetic, option restrictions, a temporary closure date |
| paraphrase | 4 | Casual wording, indirect answers (持ち物 → 特別な持ち物はございません) |
| direct | 2 | Facts that exist only in the new docs (aftercare, LINE reply hours) |
| out_of_scope | 2 | Shower; pets (trap: the director's dog) |
| symptom_refuse | 2 | Blog-inspired "will it cure me", pain after a treatment |
