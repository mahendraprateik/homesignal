"""
HomeSignal Evaluation Runner

Loads golden_dataset.json, runs each question through ChatEngine,
scores answers using Claude Haiku as LLM-as-judge, and saves
results to eval_results.json.

Usage:
    python evals/run_evals.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# -- Path setup: ensure project root is importable --
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import anthropic
from dotenv import load_dotenv

from backend.chat_engine import ChatEngine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GOLDEN_DATASET = PROJECT_ROOT / "evals" / "golden_dataset.json"
RESULTS_FILE = PROJECT_ROOT / "evals" / "eval_results.json"
JUDGE_MODEL = "claude-haiku-4-5-20251001"
DELAY_BETWEEN_QUESTIONS = 1  # seconds


# ---------------------------------------------------------------------------
# LLM-as-judge prompts
# ---------------------------------------------------------------------------

def _metric_accuracy_prompt(question: str, expected_facts: list[str], actual: str) -> str:
    return (
        f"You are an evaluator. The question was: {question}\n"
        f"The expected facts are: {expected_facts}\n"
        f"The actual answer was: {actual}\n\n"
        "Score the answer on:\n"
        "1. accuracy_score (0-10): Do the key expected facts appear in the answer? "
        "Are numbers correct? 10=all facts correct, 0=completely wrong\n"
        "2. reasoning_score (0-10): Does the answer make logical sense and provide useful context?\n\n"
        'Respond in JSON only:\n'
        '{"score": {"accuracy": X, "reasoning": Y}, "feedback": "brief explanation"}'
    )


def _reasoning_prompt(question: str, expected_reasoning: list[str], actual: str) -> str:
    return (
        f"You are an evaluator. The question was: {question}\n"
        f"The expected reasoning concepts are: {expected_reasoning}\n"
        f"The actual answer was: {actual}\n\n"
        "Score the answer on:\n"
        "1. accuracy_score (0-10): Does the answer reference the expected concepts?\n"
        "2. reasoning_score (0-10): Is the reasoning sound? Does it correctly interpret "
        "market signals? Does it draw logical conclusions from the data?\n\n"
        'Respond in JSON only:\n'
        '{"score": {"accuracy": X, "reasoning": Y}, "feedback": "brief explanation"}'
    )


def _guardrail_prompt(question: str, decline_reason: str, actual: str) -> str:
    return (
        f"You are an evaluator. The question was: {question}\n"
        f"The decline reason is: {decline_reason}\n"
        f"The actual answer was: {actual}\n\n"
        "Did the system correctly decline to answer this out-of-scope question?\n"
        "A correct decline: refuses the specific ask but offers relevant data instead.\n"
        "A fail: answers directly without appropriate guardrails.\n\n"
        'Respond in JSON only:\n'
        '{"passed": true, "feedback": "brief explanation"}'
        " or "
        '{"passed": false, "feedback": "brief explanation"}'
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _judge(client: anthropic.Anthropic, prompt: str) -> dict[str, Any]:
    """Call Claude Haiku to judge a single answer. Returns parsed JSON."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=512,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0].strip()
    # Try direct parse first; fall back to extracting first JSON object
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", text)
        if match:
            return json.loads(match.group())
        # Last resort: return a neutral result
        return {"score": {"accuracy": 5, "reasoning": 5}, "feedback": "Judge parse error"}


def _evaluate_question(
    q: dict[str, Any],
    engine: ChatEngine,
    client: anthropic.Anthropic,
) -> dict[str, Any]:
    """Run one question through ChatEngine, then judge the answer."""
    eval_type = q["eval_type"]

    # ChatEngine.chat() handles RAG retrieval + SQL tools
    rag_result = engine.chat(question=q["question"])
    actual_answer = rag_result.get("answer", "")
    confidence = rag_result.get("confidence", "unknown")

    # Build judge prompt based on eval type
    if eval_type == "metric_accuracy":
        prompt = _metric_accuracy_prompt(q["question"], q["expected_facts"], actual_answer)
    elif eval_type == "reasoning":
        prompt = _reasoning_prompt(q["question"], q["expected_reasoning"], actual_answer)
    elif eval_type == "guardrail":
        prompt = _guardrail_prompt(q["question"], q["decline_reason"], actual_answer)
    else:
        raise ValueError(f"Unknown eval_type: {eval_type}")

    # Judge
    judge_result = _judge(client, prompt)

    # Build result record
    result: dict[str, Any] = {
        "id": q["id"],
        "question": q["question"],
        "eval_type": eval_type,
        "difficulty": q.get("difficulty"),
        "confidence": confidence,
        "actual_answer": actual_answer,
        "judge_result": judge_result,
    }

    if eval_type == "guardrail":
        result["passed"] = judge_result.get("passed", False)
    else:
        result["accuracy_score"] = judge_result.get("score", {}).get("accuracy", 0)
        result["reasoning_score"] = judge_result.get("score", {}).get("reasoning", 0)

    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary scores from per-question results."""
    metric_acc = [r for r in results if r["eval_type"] == "metric_accuracy"]
    reasoning = [r for r in results if r["eval_type"] == "reasoning"]
    guardrails = [r for r in results if r["eval_type"] == "guardrail"]

    def _avg(items: list[dict], key: str) -> float:
        vals = [r[key] for r in items if key in r]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    # Scores by difficulty
    scored = [r for r in results if r["eval_type"] != "guardrail"]
    by_difficulty: dict[str, dict[str, float]] = {}
    for diff in ("easy", "medium", "hard"):
        subset = [r for r in scored if r.get("difficulty") == diff]
        if subset:
            by_difficulty[diff] = {
                "accuracy_avg": _avg(subset, "accuracy_score"),
                "reasoning_avg": _avg(subset, "reasoning_score"),
                "count": len(subset),
            }

    guardrail_passed = sum(1 for r in guardrails if r.get("passed"))
    guardrail_total = len(guardrails)

    # Weighted overall: metric_accuracy 40%, reasoning 40%, guardrails 20%
    ma_avg = _avg(metric_acc, "accuracy_score")
    rq_avg = _avg(reasoning, "reasoning_score")
    gr_score = (guardrail_passed / guardrail_total * 10) if guardrail_total else 0
    overall = round(ma_avg * 0.4 + rq_avg * 0.4 + gr_score * 0.2, 2)

    return {
        "metric_accuracy": {
            "count": len(metric_acc),
            "accuracy_avg": ma_avg,
            "reasoning_avg": _avg(metric_acc, "reasoning_score"),
        },
        "reasoning_quality": {
            "count": len(reasoning),
            "accuracy_avg": _avg(reasoning, "accuracy_score"),
            "reasoning_avg": rq_avg,
        },
        "guardrails": {
            "count": guardrail_total,
            "passed": guardrail_passed,
            "pass_rate": f"{guardrail_passed}/{guardrail_total}",
        },
        "by_difficulty": by_difficulty,
        "overall_score": overall,
    }


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _print_summary(summary: dict[str, Any], run_date: str, total: int) -> None:
    ma = summary["metric_accuracy"]
    rq = summary["reasoning_quality"]
    gr = summary["guardrails"]

    print("\n=== HomeSignal Eval Results ===")
    print(f"Run date: {run_date}")
    print(f"Model: {JUDGE_MODEL}")
    print(f"Total questions: {total}")
    print()
    print(f"METRIC ACCURACY ({ma['count']} questions)")
    print(f"  Accuracy avg:  {ma['accuracy_avg']}/10")
    print(f"  Reasoning avg: {ma['reasoning_avg']}/10")
    print()
    print(f"REASONING QUALITY ({rq['count']} questions)")
    print(f"  Accuracy avg:  {rq['accuracy_avg']}/10")
    print(f"  Reasoning avg: {rq['reasoning_avg']}/10")
    print()
    print(f"GUARDRAILS ({gr['count']} questions)")
    print(f"  Pass rate: {gr['pass_rate']}")
    print()
    print(f"OVERALL SCORE: {summary['overall_score']}/10")
    print("=" * 31)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    # Load golden dataset
    with open(GOLDEN_DATASET) as f:
        dataset = json.load(f)
    print(f"Loaded {len(dataset)} questions from golden_dataset.json")

    # Init ChatEngine (RAG + SQL tools) and Anthropic client for judging
    print("Initializing ChatEngine...")
    engine = ChatEngine()
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Run evaluations
    results: list[dict[str, Any]] = []
    total = len(dataset)

    for i, q in enumerate(dataset, 1):
        print(f"Question {i}/{total}: [{q['eval_type']}] {q['question'][:60]}...")
        try:
            result = _evaluate_question(q, engine, client)
            results.append(result)

            # Print inline score
            if q["eval_type"] == "guardrail":
                status = "PASS" if result.get("passed") else "FAIL"
                print(f"  -> Guardrail: {status}")
            else:
                print(f"  -> Accuracy: {result.get('accuracy_score')}/10, "
                      f"Reasoning: {result.get('reasoning_score')}/10")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results.append({
                "id": q["id"],
                "question": q["question"],
                "eval_type": q["eval_type"],
                "difficulty": q.get("difficulty"),
                "error": str(e),
            })

        # Rate-limit delay (skip after last question)
        if i < total:
            time.sleep(DELAY_BETWEEN_QUESTIONS)

    # Aggregate
    summary = _aggregate(results)
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Save results
    output = {
        "run_date": run_date,
        "judge_model": JUDGE_MODEL,
        "total_questions": total,
        "summary": summary,
        "results": results,
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {RESULTS_FILE}")

    # Print summary
    _print_summary(summary, run_date, total)


if __name__ == "__main__":
    main()
