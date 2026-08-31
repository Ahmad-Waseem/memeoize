# Memoize — Hackathon Submission Notes

## Title + pitch

**Memoize** — Custom memes mid-conversation, without fighting the prompt.

Slack/Teams users want a reaction meme *now* — their face, their joke, their template. Raw image models need careful prompts and usually several regenerations. Memoize is an n8n agent that understands the request, engineers the image prompt, generates, verifies, and self-corrects (max 2 retries) so one chat message becomes a postable meme.

---

## 01 — Complete solution + Improvement Changelog

### Improvement Changelog

| Stage | What you tried and why | Evidence | Decision / Learning |
|-------|------------------------|----------|---------------------|
| **Baseline** | Forward raw user text (+ optional face) straight to Gemini image gen — what a naive Slack “meme bot” does | Avg judge score **2.80 / 5** on 10 fixed cases. Failures: wrong layout (`drake_01` 2), ignored labels (`distracted_bf_01` 2), scrambled panels / missing face (`face_swap_reaction_02` 2), **no image at all** when request sounded caption-only (`caption_only_01` **0**) | Starting point: generation alone is not the bottleneck — understanding + checking is |
| **Iteration 1** | Added **Understand Intent** — map casual text to structured format, composition notes, text overlays | Ambiguous chat like “that meme with the two buttons” → Two Buttons (`ambiguous_format_01`: 3→4). Layout/label wins on Drake / Distracted BF | **Kept.** Intent grounding fixes “I know what I mean, the model doesn’t” |
| **Iteration 2** | Added **Engineer Prompt** — turn structured intent into an explicit image-edit prompt before gen | Biggest jump case: `caption_only_01` baseline returned text only (**0**); agent produced full expanding-brain meme (**4**) | **Kept.** This step is where most of the value lives vs one-shot |
| **Iteration 3** | Added **Verify (vision) + retry loop** (max 2) with corrective feedback into prompt engineering | Retry rate **20%** (`caption_only_01`, `animal_meme_01`, both used **2** retries). Face/layout cases that baseline scrambled often land at **5** when prompt eng is strong | **Kept.** Retry is insurance; prompt eng does most of the lift |
| **Experiment removed** | **Hard decline on any minor-adjacent template** (e.g. Success Kid / cat face) before generation | Blocked usable demos; later policy = only explicit sexual / graphic violence / hate. With updated policy, `animal_meme_01` agent scored **4** (2 retries) | **Removed strict decline.** Over-refusing ≠ safety; gate explicit harm, don’t kill the product |
| **Also dropped** | Judging only via flaky n8n prod webhooks for the scored eval | Empty HTTP 200s / publish confusion burned runs | **Switched scored eval to `eval.py --local`** (same models/pipeline logic). n8n stays for live demo |
| **Final** | Intent → engineer → generate → verify/retry on n8n | **Baseline 2.80 → Agent 4.30 (+1.50, +54%)** on same 10 cases; agent better on **6**, tie **3**, worse **1** (`hard_case` 3→2) | Main contribution: **prompt engineering after structured intent**, with verify/retry as the self-enhancing loop |

### How you evaluate

| Metric | Simple baseline | Agent solution | Change |
|--------|-----------------|----------------|--------|
| **Primary: LLM-as-judge meme quality (1–5)** | **2.80** | **4.30** | **+1.50 (+54%)** |
| Cases needing self-correction | N/A (one-shot) | **20%** (2/10) | Automatic recovery on hard prompts |
| Human prompt iterations mid-chat | Many (user regenerates) | **One request** | Designed for Slack/Teams pace |

**Rubric (judge):** layout fidelity, text legibility, face/reference use when provided, match to requested format — same cases for both paths (`eval/test_cases.json`). Full scores: `eval/results/eval_results.json`. Charts: `report.md`.

**Hard case:** `hard_case_conflicting_instructions` (face + top caption + different bottom person). Agent **2** vs baseline **3** — multi-constraint prompts can still collapse; verify didn’t always catch missing caption. Revealed: retry feedback must name *each* constraint, not “looks wrong.”

### Hot take / insight

Image models don’t mainly fail at “drawing a face” — they fail at **placement, format, and text**. A one-shot chat prompt skips that reasoning. The agent’s win is forcing structure *before* pixels, then checking *after*. Over-strict safety that declines whole meme templates taught us: product-killing refusals ≠ responsible agents.

---

## 02 — Reproduction guide

See `REPRODUCTION.md` for clean-environment setup, baseline vs agent commands, models, cost/runtime.

Quick path:

```powershell
cd eval
$env:GEMINI_API_KEY = "YOUR_KEY"
..\ .venv\Scripts\python.exe eval.py --local
```

Submission zip (tracked files only): `D:\repos\memeoize-submission.zip`  
Repo: https://github.com/Ahmad-Waseem/memeoize

---

## 03 — Solution video outline (~5 min)

1. Problem: Slack/Teams joke dies while prompting  
2. Baseline one-shot fail (e.g. `caption_only_01` or Drake)  
3. Full agent run in n8n (intent → engineer → gen → verify)  
4. Side-by-side + **2.80 → 4.30**  
5. Changelog: biggest win = prompt eng; removed = over-strict Success Kid decline  

---

## 04 — Agent trajectories

- Representative phase traces: `eval/results/trajectories.json`
- Live runs: n8n Executions tab on published `meme-agent` / `meme-baseline`
- Scored truth for judges: **`eval/results/eval_results.json`** and **`report.md`** (baseline **2.80**, agent **4.30**) — prefer these over any older summary numbers inside trajectories
