# Reproduction Guide

Starting from a clean environment (no prior n8n or API setup).

## 1. Prerequisites

- Node.js installed (for `npx`)
- Python 3.9+ (for the eval script)
- A free personal Gemini API key: https://aistudio.google.com/apikey
  (no billing required for the volume used here — free tier covers it)

## 2. Start n8n locally

```bash
npx n8n
```

Open http://localhost:5678 in your browser. Create a local account if
prompted (stays on your machine, no cloud account needed).

## 3. Add your Gemini credential

In n8n: **Credentials → New → HTTP Header Auth**
- Name: `x-goog-api-key`
- Value: `<your Gemini API key>`

(This matches the `genericCredentialType: httpHeaderAuth` used in both
workflow JSON files — attach this credential to every HTTP Request node
that calls `generativelanguage.googleapis.com`.)

## 4. Import both workflows

In n8n: **Workflows → Import from File**
- `workflows/baseline.json`
- `workflows/agent.json`

Activate both (toggle in the top-right of each workflow).

## 5. Point the "Load Prompts" node at your local prompts folder

The `agent.json` workflow's "Load Prompts" code node reads the three
system prompts from disk. Update the `base` path in that node to wherever
you've placed the `prompts/` folder locally (defaults to
`/home/claude/meme-agent/prompts/` — change to your own path).

## 6. Run the evaluation

```bash
cd eval
pip install requests
export GEMINI_API_KEY=<your key>
python eval.py
```

This runs all 10 cases in `test_cases.json` against both webhooks, saves
generated images to `eval/results/`, and prints a summary comparison.

## 7. Manual single-request test (optional, for the demo video)

```bash
curl -X POST http://localhost:5678/webhook/meme-agent \
  -H "Content-Type: application/json" \
  -d '{"text": "Drake meme, disapprove writing regex by hand, approve asking an agent"}'
```

## Expected output

- Each webhook call returns `{ "image": "<base64 PNG>", ... }`
- Full eval run (10 cases × baseline + agent): ~2-4 minutes, well under
  $1 in API cost on Gemini's free/low tier
- Agent trajectory (per-request reasoning + retries) is visible in n8n's
  **Executions** tab for the `agent.json` workflow — click any execution to
  see each node's input/output, including verify-step verdicts and any
  retry loops triggered

## Versions used

- n8n: latest via `npx n8n` (pin version if you need exact reproducibility:
  `npx n8n@<version>`)
- Gemini models: `gemini-2.5-flash` (text/reasoning steps),
  `gemini-2.5-flash-image` (generation) — update model names in the
  workflow JSON if these are deprecated by the time you run this
