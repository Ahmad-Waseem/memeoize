"""
Evaluation script for the Meme Agent hackathon submission.

Runs a fixed set of test cases against BOTH the baseline and the full agent,
then scores outputs with an LLM-as-judge (Gemini) pass.

Usage:
    python eval.py              # via n8n webhooks (requires published workflows)
    python eval.py --local      # direct Gemini API, no n8n needed

Prereqs:
    - GEMINI_API_KEY env var set
    - For webhook mode: n8n running with meme-baseline + meme-agent published
    - test_cases.json populated with your test set

Resume behavior:
    - Skips baseline/agent generation if the PNG already exists in eval/results/
    - Rewrites eval/results/eval_results.json after EACH case completes
    - Use --force to regenerate images even when PNGs exist
"""

import argparse
import base64
import json
import os
import re
import time
from pathlib import Path

import requests

N8N_BASE = "http://localhost:5678/webhook"
OUT_DIR = Path(__file__).parent / "results"
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
RESULTS_PATH = OUT_DIR / "eval_results.json"
OUT_DIR.mkdir(exist_ok=True)

TEXT_MODEL = "gemini-3.6-flash"
IMAGE_MODEL = "gemini-2.5-flash-image"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def load_test_cases():
    with open(Path(__file__).parent / "test_cases.json") as f:
        return json.load(f)


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def parse_json_response(text: str) -> dict:
    clean = re.sub(r"```json|```", "", text).strip()
    return json.loads(clean)


def load_existing_rows() -> dict[str, dict]:
    if not RESULTS_PATH.exists():
        return {}
    try:
        rows = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        return {row["id"]: row for row in rows}
    except (json.JSONDecodeError, KeyError):
        return {}


def save_rows(rows: list[dict]) -> None:
    RESULTS_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def save_progress(cases: list, rows_by_id: dict) -> None:
    ordered = []
    for j, c in enumerate(cases):
        cid = c.get("id", f"case_{j}")
        if cid in rows_by_id:
            ordered.append(rows_by_id[cid])
    save_rows(ordered)


def png_path(case_id: str, side: str) -> Path:
    return OUT_DIR / f"{case_id}_{side}.png"


def load_png_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def print_case_result(case_id: str, row: dict) -> None:
    print(f"[{case_id}] saved", flush=True)
    print(
        f"  baseline: {row.get('baseline_score', '?')}/5 — {row.get('baseline_reasoning', '')}",
        flush=True,
    )
    agent_line = f"  agent:    {row.get('agent_score', '?')}/5 — {row.get('agent_reasoning', '')}"
    if "agent_retries_used" in row:
        agent_line += f" (retries: {row['agent_retries_used']})"
    print(agent_line, flush=True)


def gemini_call(api_key: str, model: str, body: dict, timeout: int = 300) -> dict:
    url = f"{API_BASE}/{model}:generateContent?key={api_key}"
    last_err = None
    for attempt in range(4):
        try:
            resp = requests.post(url, json=body, timeout=timeout)
            if resp.status_code in (429, 503):
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            time.sleep(5 * (attempt + 1))
    if last_err:
        raise last_err
    raise RuntimeError(f"Gemini call failed after retries ({model})")


def gemini_json(api_key: str, system_prompt: str, user_parts: list) -> dict:
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": user_parts}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    result = gemini_call(api_key, TEXT_MODEL, body)
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    return parse_json_response(text)


def extract_image_b64(gemini_response: dict) -> str | None:
    parts = gemini_response.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    for part in parts:
        inline = part.get("inline_data") or part.get("inlineData")
        if inline and inline.get("data"):
            return inline["data"]
    return None


def generate_image(api_key: str, prompt: str, reference_b64: str | None = None) -> str:
    parts = [{"text": prompt}]
    if reference_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": reference_b64}})
    body = {"contents": [{"parts": parts}]}
    result = gemini_call(api_key, IMAGE_MODEL, body, timeout=300)
    image_b64 = extract_image_b64(result)
    if not image_b64:
        err = result.get("error") or str(parts)
        raise RuntimeError(f"Image model returned no image: {err}")
    return image_b64


def run_baseline_local(text: str, image_b64: str | None, api_key: str) -> dict:
    image = generate_image(api_key, text, image_b64)
    return {"image": image, "mode": "baseline"}


def run_agent_local(text: str, image_b64: str | None, api_key: str, max_retries: int = 2) -> dict:
    understand_prompt = load_prompt("01_understand_intent.md")
    engineer_prompt = load_prompt("02_engineer_prompt.md")
    verify_prompt = load_prompt("03_verify_output.md")

    user_parts = [{"text": f"user_text: {text}\nhas_reference_image: {bool(image_b64)}"}]
    if image_b64:
        user_parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})

    intent = gemini_json(api_key, understand_prompt, user_parts)
    if intent.get("safety_flag") == "needs_review":
        return {"error": "request_declined", "reason": intent.get("safety_reason", "safety review")}

    previous_feedback = None
    retries_used = 0
    verdict = "pass"
    generated_image_b64 = None
    image_edit_prompt = None

    for attempt in range(max_retries + 1):
        engineer_input = {"intent": intent, "previous_attempt_feedback": previous_feedback}
        engineered = gemini_json(
            api_key,
            engineer_prompt,
            [{"text": json.dumps(engineer_input)}],
        )
        image_edit_prompt = engineered.get("image_edit_prompt")
        if not image_edit_prompt:
            return {"error": "prompt_engineering_failed", "reason": engineered.get("reasoning", "no prompt")}

        generated_image_b64 = generate_image(api_key, image_edit_prompt, image_b64)

        verify_parts = [
            {
                "text": (
                    f"image_edit_prompt: {image_edit_prompt}\n"
                    f"composition_notes: {intent.get('composition_notes', '')}"
                )
            },
            {"inline_data": {"mime_type": "image/png", "data": generated_image_b64}},
        ]
        review = gemini_json(api_key, verify_prompt, verify_parts)
        verdict = review.get("verdict", "pass")

        if verdict == "pass" or attempt >= max_retries:
            break

        retries_used += 1
        previous_feedback = review.get("retry_feedback")

    return {
        "image": generated_image_b64,
        "mode": "agent",
        "retries_used": retries_used,
        "verdict": verdict if verdict == "pass" else "retry_budget_exhausted",
    }


def call_webhook(path: str, text: str, image_b64: str | None):
    payload = {"text": text}
    if image_b64:
        payload["image"] = image_b64
    resp = requests.post(f"{N8N_BASE}/{path}", json=payload, timeout=180)
    resp.raise_for_status()
    if not resp.text.strip():
        raise RuntimeError(
            f"{path} returned HTTP {resp.status_code} with empty body — "
            "workflow not reaching Respond node (check credential + Publish), "
            "or run: python eval.py --local"
        )
    return resp.json()


def judge_image(image_b64: str, request_text: str, api_key: str) -> dict:
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "You are scoring a generated meme image on a 1-5 scale for "
                            f'how well it satisfies this request: "{request_text}". '
                            "Consider: is the composition correct, "
                            "is any face/likeness correctly placed and undistorted, "
                            "does it look like a real postable meme rather than a broken "
                            'generation. Respond ONLY as JSON: '
                            '{"score": <int 1-5>, "reasoning": "<one sentence>"}'
                        )
                    },
                    {"inline_data": {"mime_type": "image/png", "data": image_b64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    result = gemini_call(api_key, TEXT_MODEL, body)
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    return parse_json_response(text)


def case_fully_done(row: dict) -> bool:
    cid = row["id"]
    return (
        png_path(cid, "baseline").exists()
        and png_path(cid, "agent").exists()
        and "baseline_score" in row
        and "agent_score" in row
        and "baseline_reasoning" in row
        and "agent_reasoning" in row
    )


def main():
    parser = argparse.ArgumentParser(description="Run meme agent eval suite")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Call Gemini directly (no n8n). Same pipeline logic, bypasses webhooks.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate images even when PNGs already exist in eval/results/.",
    )
    args = parser.parse_args()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY env var before running eval.")

    cases = load_test_cases()
    existing = load_existing_rows()
    rows_by_id: dict[str, dict] = {**existing}

    mode_label = "local (direct Gemini)" if args.local else "n8n webhooks"
    print(f"Running eval via {mode_label}", flush=True)
    if not args.force:
        print("Resume mode: skipping cases with existing PNGs/scores (use --force to redo)", flush=True)
    print(flush=True)

    for i, case in enumerate(cases):
        case_id = case.get("id", f"case_{i}")
        text = case["text"]
        row = rows_by_id.get(case_id, {"id": case_id, "text": text})
        row["id"] = case_id
        row["text"] = text

        if not args.force and case_fully_done(row):
            print(f"[{case_id}] skip — already complete", flush=True)
            print_case_result(case_id, row)
            continue

        image_path = case.get("image_path")
        image_b64 = None
        if image_path:
            image_b64 = base64.b64encode(
                (Path(__file__).parent / image_path).read_bytes()
            ).decode()

        baseline_png = png_path(case_id, "baseline")
        baseline_cached = False
        if not args.force and baseline_png.exists():
            baseline_cached = True
            print(f"[{case_id}] baseline PNG exists — skipping generation", flush=True)
            baseline_img = load_png_b64(baseline_png)
            baseline_result = {"image": baseline_img, "mode": "baseline", "cached": True}
        else:
            print(f"[{case_id}] running baseline...", flush=True)
            try:
                baseline_result = (
                    run_baseline_local(text, image_b64, api_key)
                    if args.local
                    else call_webhook("meme-baseline", text, image_b64)
                )
            except Exception as e:
                print(f"[{case_id}] baseline error: {e}", flush=True)
                baseline_result = {"error": str(e)}
            baseline_img = baseline_result.get("image")
            if baseline_img and not baseline_result.get("cached"):
                baseline_png.write_bytes(base64.b64decode(baseline_img))
                print(f"[{case_id}] baseline image saved", flush=True)

        if baseline_img:
            if (
                not args.force
                and baseline_cached
                and row.get("baseline_score") is not None
                and row.get("baseline_reasoning")
            ):
                pass
            else:
                try:
                    judged = judge_image(baseline_img, text, api_key)
                    row["baseline_score"] = judged["score"]
                    row["baseline_reasoning"] = judged["reasoning"]
                except Exception as e:
                    row["baseline_score"] = 0
                    row["baseline_reasoning"] = f"judge failed: {e}"
                    print(f"[{case_id}] baseline judge error: {e}", flush=True)
        else:
            row["baseline_score"] = 0
            row["baseline_reasoning"] = baseline_result.get(
                "reason", baseline_result.get("error", "no image returned")
            )

        rows_by_id[case_id] = row
        save_progress(cases, rows_by_id)
        print(
            f"[{case_id}] baseline done: {row.get('baseline_score', '?')}/5",
            flush=True,
        )

        agent_png = png_path(case_id, "agent")
        agent_cached = False
        if not args.force and agent_png.exists():
            agent_cached = True
            print(f"[{case_id}] agent PNG exists — skipping generation", flush=True)
            agent_img = load_png_b64(agent_png)
            agent_result = {
                "image": agent_img,
                "mode": "agent",
                "cached": True,
                "retries_used": row.get("agent_retries_used", 0),
            }
        else:
            print(f"[{case_id}] running agent...", flush=True)
            try:
                agent_result = (
                    run_agent_local(text, image_b64, api_key)
                    if args.local
                    else call_webhook("meme-agent", text, image_b64)
                )
            except Exception as e:
                print(f"[{case_id}] agent error: {e}", flush=True)
                agent_result = {"error": str(e)}
            agent_img = agent_result.get("image")
            if agent_img and not agent_result.get("cached"):
                agent_png.write_bytes(base64.b64decode(agent_img))
                print(f"[{case_id}] agent image saved", flush=True)

        if agent_img:
            if (
                not args.force
                and agent_cached
                and row.get("agent_score") is not None
                and row.get("agent_reasoning")
            ):
                row["agent_retries_used"] = agent_result.get(
                    "retries_used", row.get("agent_retries_used", 0)
                )
            else:
                try:
                    judged = judge_image(agent_img, text, api_key)
                    row["agent_score"] = judged["score"]
                    row["agent_reasoning"] = judged["reasoning"]
                    row["agent_retries_used"] = agent_result.get("retries_used", 0)
                except Exception as e:
                    row["agent_score"] = 0
                    row["agent_reasoning"] = f"judge failed: {e}"
                    row["agent_retries_used"] = agent_result.get("retries_used", 0)
                    print(f"[{case_id}] agent judge error: {e}", flush=True)
        else:
            row["agent_score"] = 0
            row["agent_reasoning"] = agent_result.get(
                "reason", agent_result.get("error", "no image returned")
            )

        rows_by_id[case_id] = row
        save_progress(cases, rows_by_id)
        print_case_result(case_id, row)
        time.sleep(1)

    rows = []
    for j, c in enumerate(cases):
        cid = c.get("id", f"case_{j}")
        if cid in rows_by_id:
            rows.append(rows_by_id[cid])

    baseline_avg = sum(r.get("baseline_score", 0) for r in rows) / len(rows)
    agent_avg = sum(r.get("agent_score", 0) for r in rows) / len(rows)
    retry_rate = sum(1 for r in rows if r.get("agent_retries_used", 0) > 0) / len(rows)

    print("\n=== SUMMARY ===", flush=True)
    print(f"Cases run:            {len(rows)}", flush=True)
    print(f"Baseline avg score:   {baseline_avg:.2f} / 5", flush=True)
    print(f"Agent avg score:      {agent_avg:.2f} / 5", flush=True)
    print(f"Cases needing retry:  {retry_rate * 100:.0f}%", flush=True)
    print(f"\nFull results + images saved to {OUT_DIR}/", flush=True)


if __name__ == "__main__":
    main()
