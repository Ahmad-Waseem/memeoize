"""
Evaluation script for the Meme Agent hackathon submission.

Runs a fixed set of test cases against BOTH the baseline webhook and the
full agent webhook, saves output images, and produces a scored comparison
table using an LLM-as-judge (Gemini) pass, plus a slot for manual override.

Usage:
    python eval.py

Prereqs:
    - n8n running locally with both workflows imported and active
      (baseline at /webhook/meme-baseline, agent at /webhook/meme-agent)
    - GEMINI_API_KEY set as an env var (used here only for the judge pass,
      separate from whatever n8n uses internally)
    - test_cases.json populated with your actual test set
"""

import base64
import json
import os
import time
from pathlib import Path

import requests

N8N_BASE = "http://localhost:5678/webhook"
OUT_DIR = Path(__file__).parent / "results"
OUT_DIR.mkdir(exist_ok=True)

JUDGE_MODEL_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def load_test_cases():
    with open(Path(__file__).parent / "test_cases.json") as f:
        return json.load(f)


def call_webhook(path: str, text: str, image_b64: str | None):
    payload = {"text": text}
    if image_b64:
        payload["image"] = image_b64
    resp = requests.post(f"{N8N_BASE}/{path}", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def judge_image(image_b64: str, request_text: str) -> dict:
    """LLM-as-judge: scores a generated meme image against the original request.
    Returns {"score": 1-5, "reasoning": str}. Treat as a starting signal —
    spot-check a sample manually before trusting it in your writeup."""
    api_key = os.environ["GEMINI_API_KEY"]
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "You are scoring a generated meme image on a 1-5 scale for "
                            "how well it satisfies this request: "
                            f'"{request_text}". Consider: is the composition correct, '
                            "is any face/likeness correctly placed and undistorted, "
                            "does it look like a real postable meme rather than a broken "
                            "generation. Respond ONLY as JSON: "
                            '{"score": <int 1-5>, "reasoning": "<one sentence>"}'
                        )
                    },
                    {"inline_data": {"mime_type": "image/png", "data": image_b64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    resp = requests.post(
        f"{JUDGE_MODEL_URL}?key={api_key}", json=body, timeout=60
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def main():
    cases = load_test_cases()
    rows = []

    for i, case in enumerate(cases):
        case_id = case.get("id", f"case_{i}")
        text = case["text"]
        image_path = case.get("image_path")
        image_b64 = None
        if image_path:
            image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode()

        print(f"[{case_id}] running baseline...")
        baseline_result = call_webhook("meme-baseline", text, image_b64)
        baseline_img = baseline_result.get("image")

        print(f"[{case_id}] running agent...")
        agent_result = call_webhook("meme-agent", text, image_b64)
        agent_img = agent_result.get("image")

        row = {"id": case_id, "text": text}

        if baseline_img:
            (OUT_DIR / f"{case_id}_baseline.png").write_bytes(
                base64.b64decode(baseline_img)
            )
            judged = judge_image(baseline_img, text)
            row["baseline_score"] = judged["score"]
            row["baseline_reasoning"] = judged["reasoning"]
        else:
            row["baseline_score"] = 0
            row["baseline_reasoning"] = "no image returned"

        if agent_img:
            (OUT_DIR / f"{case_id}_agent.png").write_bytes(
                base64.b64decode(agent_img)
            )
            judged = judge_image(agent_img, text)
            row["agent_score"] = judged["score"]
            row["agent_reasoning"] = judged["reasoning"]
            row["agent_retries_used"] = agent_result.get("retries_used", 0)
        else:
            row["agent_score"] = 0
            row["agent_reasoning"] = agent_result.get("reason", "no image returned")

        rows.append(row)
        time.sleep(1)  # be polite to local n8n + API rate limits

    with open(OUT_DIR / "eval_results.json", "w") as f:
        json.dump(rows, f, indent=2)

    # Summary
    baseline_avg = sum(r["baseline_score"] for r in rows) / len(rows)
    agent_avg = sum(r["agent_score"] for r in rows) / len(rows)
    retry_rate = sum(1 for r in rows if r.get("agent_retries_used", 0) > 0) / len(rows)

    print("\n=== SUMMARY ===")
    print(f"Cases run:            {len(rows)}")
    print(f"Baseline avg score:   {baseline_avg:.2f} / 5")
    print(f"Agent avg score:      {agent_avg:.2f} / 5")
    print(f"Cases needing retry:  {retry_rate * 100:.0f}%")
    print(f"\nFull results + images saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
