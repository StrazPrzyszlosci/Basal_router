#!/home/krzysiek/basal-env/bin/python
"""GEPA optimization of the router's prompt components (instructions + model cards).

Every rollout is a basal routing decision (~1 min on this CPU) and every reflective proposal is
one opencode call via routerd, so the default budget is deliberately micro (max_metric_calls).
The evolved candidate is saved as an artifact — it is NOT applied to models.json automatically.

Usage:
  ~/basal-env/bin/python opt/gepa_optimize.py --tasks 6 --max-metric-calls 18
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import router  # noqa: E402

import gepa  # noqa: E402
from gepa.core.adapter import EvaluationBatch  # noqa: E402

ROUTERD = "http://127.0.0.1:8788/v1/chat/completions"

REFLECTION_TEMPLATE = """You are improving the prompt of a small classifier that routes tasks to free LLMs.
Component to improve: {component}
Current component text:
{current}

Valid model keys: {keys}
Recent routing decisions (fields: task, gold = the correct model, chosen, confidence, top3):
{decisions}

Rewrite the component text so the classifier picks the gold model more often.
Rules: under 60 words; factual; keep the same language as the current text.
Output ONLY the new component text, nothing else."""


def build_candidate_components():
    data = router.load_registry()
    components = {"instructions": router.INSTRUCTIONS}
    for m in data["models"]:
        components[f"card:{m['key']}"] = m["card"]
    return components


def reflect_via_routerd(prompt: str) -> str:
    response = httpx.post(ROUTERD,
                          json={"model": "auto", "messages": [{"role": "user", "content": prompt}]},
                          timeout=600)
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"].strip()
    marker = "\n---\n[router]"
    if marker in text:
        text = text.split(marker)[0].strip()
    return text


class RouterAdapter:
    """GEPAAdapter over the basal router: rollouts are routing decisions, scores are gold hits."""

    def __init__(self, keys):
        self.keys = keys

    def _request(self, candidate, task):
        criteria = {k: candidate[f"card:{k}"] for k in self.keys}
        return {"state": task["task"], "lang": "pl",
                "questions": {"model": {"type": "choice",
                                        "instructions": candidate["instructions"],
                                        "criteria": criteria}}}

    def evaluate(self, batch, candidate, capture_traces=False, **kwargs):
        outputs, scores, trajectories = [], [], []
        for task in batch:
            chosen, conf, top3 = "", 0.0, []
            try:
                response = httpx.post(f"{router.BASAL_URL}/v1/systemone",
                                      json=self._request(candidate, task), timeout=300)
                response.raise_for_status()
                answer = response.json()["answers"]["model"]
                chosen = answer["choice"]
                conf = answer["confidence"]
                top3 = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])[:3]
            except Exception as exc:
                chosen = f"error: {str(exc)[:150]}"
            correct = chosen == task["gold"]
            outputs.append(chosen)
            scores.append(1.0 if correct else 0.0)
            trajectories.append({"task": task["task"], "gold": task["gold"],
                                 "chosen": chosen, "confidence": round(conf, 3),
                                 "top3": [[k, round(v, 3)] for k, v in top3],
                                 "correct": correct})
        print(f"[adapter] evaluated {len(batch)} tasks, hits: {int(sum(scores))}", flush=True)
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update, **kwargs):
        rows = [t for t, s in zip(eval_batch.trajectories, eval_batch.scores)]
        dataset = {}
        for component in components_to_update:
            dataset[component] = [{"component_text": candidate.get(component, ""),
                                   "decisions": rows[-8:]}]
        return dataset

    def propose_new_texts(self, candidate, reflective_dataset, components_to_update, **kwargs):
        proposals = {}
        for component in components_to_update:
            rows = reflective_dataset.get(component) or []
            decisions = json.dumps(rows[0]["decisions"] if rows else [], ensure_ascii=False)
            prompt = REFLECTION_TEMPLATE.format(
                component=component,
                current=candidate.get(component, ""),
                decisions=decisions,
                keys=", ".join(self.keys))
            try:
                new_text = reflect_via_routerd(prompt)
                print(f"[reflection] {component}: {len(new_text)} chars via routerd", flush=True)
            except Exception as exc:
                print(f"[reflection] {component}: routerd failed ({str(exc)[:120]}), keeping current", flush=True)
                new_text = ""
            proposals[component] = new_text or candidate.get(component, "")
        return proposals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=6)
    parser.add_argument("--max-metric-calls", type=int, default=18)
    parser.add_argument("--out", default=str(ROOT / "opt" / "gepa_result"))
    args = parser.parse_args()

    bench = [json.loads(line) for line in
             (ROOT / "experiments" / "benchmark_tasks.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    trainset = bench[:args.tasks]
    keys = [m["key"] for m in router.load_registry()["models"]]
    adapter = RouterAdapter(keys)

    t0 = time.time()
    result = gepa.optimize(
        seed_candidate=build_candidate_components(),
        trainset=trainset,
        adapter=adapter,
        max_metric_calls=args.max_metric_calls,
        reflection_minibatch_size=2,
        run_dir=args.out,
        display_progress_bar=False,
        raise_on_exception=False,
    )

    best = getattr(result, "best_candidate", None) or {}
    scores = getattr(result, "val_aggregate_scores", None)
    best_idx = getattr(result, "best_idx", None)
    score = None
    if scores is not None and best_idx is not None:
        try:
            score = scores[best_idx]
        except Exception:
            score = None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"best_candidate": best, "score": score,
               "seed": build_candidate_components(),
               "tasks": args.tasks, "max_metric_calls": args.max_metric_calls,
               "seconds": round(time.time() - t0, 1),
               "meta": {"model": "basal-2.0-mini 1.5B Q8_0", "reflection_lm": "routerd (auto)"}}
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"score": score, "best_keys": list(best)[:5],
                      "seconds": round(time.time() - t0, 1)}, ensure_ascii=False), flush=True)
    print("DONE: " + str(out / "result.json"), flush=True)


if __name__ == "__main__":
    main()
