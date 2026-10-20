#!/home/krzysiek/basal-env/bin/python
"""OpenEvolve scaffold: evolve the router's model cards as code against routing accuracy.

Why this is a scaffold and not a run: OpenEvolve is LLM-call-bound (every iteration needs several
LLM calls to mutate code, plus an evaluator pass that is 6+ basal decisions on this CPU). Over
free opencode endpoints that means days for a meaningful population. The components below are
complete — point OpenEvolve's LLM at a fast endpoint (or run overnight) and execute.

Layout expected by OpenEvolve:
  initial_program.py  — the genome: a function `route_cards()` returning the card text per key
  evaluator.py        — `evaluate(program_path)` -> float (routing accuracy on the eval subset)

Run (with a fast LLM configured, e.g. via OPENAI_BASE_URL / OPENAI_API_KEY pointing at a fast
endpoint — routerd works but is slow for evolution):
  openevolve-run opt/openevolve_initial_program.py opt/openevolve_evaluator.py --config opt/openevolve_config.yaml

`--baseline` here measures the current cards on the eval subset without any evolution.
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import router  # noqa: E402

EVAL_TASKS = [json.loads(line) for line in
              (ROOT / "experiments" / "benchmark_tasks.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()][:6]


def evaluate_cards(cards: dict) -> float:
    criteria = {k: cards[k] for k in cards}
    score = 0.0
    for task in EVAL_TASKS:
        request = {"state": task["task"], "lang": "pl",
                   "questions": {"model": {"type": "choice",
                                           "instructions": router.INSTRUCTIONS,
                                           "criteria": criteria}}}
        try:
            response = httpx.post(f"{router.BASAL_URL}/v1/systemone", json=request, timeout=300)
            response.raise_for_status()
            chosen = response.json()["answers"]["model"]["choice"]
            score += 1.0 if chosen == task["gold"] else 0.0
        except Exception:
            pass
    return score / len(EVAL_TASKS)


def current_cards():
    return {m["key"]: m["card"] for m in router.load_registry()["models"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true",
                        help="measure the current cards on the eval subset (6 basal calls)")
    args = parser.parse_args()
    if args.baseline:
        print(json.dumps({"baseline_accuracy": evaluate_cards(current_cards())}, indent=2))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
