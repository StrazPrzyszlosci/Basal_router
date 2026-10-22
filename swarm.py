#!/home/krzysiek/basal-env/bin/python
"""Swarm orchestration on the free-model router: basal delegates every step.

Two modes (also exposed as virtual models "swarm" / "ultra-swarm" on routerd :8788):

  swarm      basal picks the decomposer -> it writes self-contained subagent tasks ->
             for EACH subtask basal again picks the subagent model (respecting per-provider
             RPM windows and 429 cooldowns) -> subtasks run in parallel -> basal picks the
             synthesizer that merges everything into the final answer.

  ultra      basal picks the planner -> plan -> basal picks the critic -> {"ok", "fixes"}
             (one revision round when fixes are required) -> basal picks the swarm manager ->
             it turns the approved plan into subagent assignments -> per subtask basal picks
             the subagent model (RPM-aware) -> parallel execution -> basal picks the
             synthesizer.

Every basal decision, fallback and RPM wait is journaled in the returned trace, and the
final answer carries a router-style footer listing which model handled which role.

Usage:
  ~/basal-env/bin/python swarm.py "task" [--mode swarm|ultra] [--parallel 2] [--max-subtasks 4]
"""
import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "opt"))
import router  # noqa: E402
import rpm  # noqa: E402

ROLE_TASKS = {
    "planner": "Create a concise numbered action plan for a complex multi-step task.",
    "critic": "Review an action plan and judge whether it is correct, complete and executable.",
    "manager": "Turn an approved action plan into concrete subagent task assignments.",
    "decomposer": "Split a complex task into independent subagent task prompts.",
    "aggregator": "Merge several subagent answers into one final complete answer.",
    "executor": "Execute a single self-contained subagent task.",
}
MAX_FALLBACKS = 2
RESULT_CHARS = 6000


def parse_json(text):
    start, end = str(text).find("{"), str(text).rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def available_models():
    """usable_models minus providers with no RPM headroom or an active 429 cooldown.
    If everything is exhausted: bounded wait for headroom, then best effort with a note."""
    models = router.usable_models()
    if router.rpm is None or not router.load_settings().get("respect_rate_limits", True):
        return models, None
    ok = [m for m in models if rpm.available(m)]
    if ok:
        return ok, None
    providers = {rpm.provider_of(m) for m in models}
    wait = rpm.wait_headroom(providers, max_wait_s=90)
    if wait:
        time.sleep(min(wait, 90))
        ok = [m for m in models if rpm.available(m)]
        if ok:
            return ok, f"waited {round(min(wait, 90))}s for RPM headroom"
    return (ok or models), "all providers at their RPM limit — proceeding best-effort"


def _execute_with_fallback(decision, prompt, timeout_s):
    """Run on basal's pick; on failure walk down the ranking (routerd fallback pattern)."""
    candidates = [decision["model"]]
    candidates += [r["model"] for r in decision.get("ranking", [])
                   if r["model"] != decision["model"]][:MAX_FALLBACKS]
    errors = []
    for key in candidates:
        try:
            return router.execute(key, prompt, timeout_s), []
        except Exception as exc:
            errors.append(f"{key}: {str(exc)[:200]}")
    raise RuntimeError("; ".join(errors) or "no model executed")


def call_role(role, prompt, trace, timeout_s, note=None):
    """basal picks the model for `role`, then that model answers `prompt`."""
    routing_task = ROLE_TASKS[role]
    models, rpm_note = available_models()
    if not models:
        raise RuntimeError(router.NO_MODELS_HINT)
    decision = router.route(routing_task, models)
    step = {"role": role, "picked": decision["model"], "model_id": decision["model_id"],
            "chosen_by_basal": decision["chosen_by_basal"], "confidence": decision["confidence"],
            "deferred": decision["deferred"]}
    if rpm_note:
        step["rpm_note"] = rpm_note
    if note:
        step["note"] = note
    trace.append(step)
    text, errors = _execute_with_fallback(decision, prompt, timeout_s)
    if errors:
        step["fallback_errors"] = errors
        entry = next((m for m in router.load_registry()["models"]
                      if m["key"] == decision["model"]), {})
        step["model_id"] = entry.get("id", step["model_id"])
    return text


def parse_subtasks(text, max_subtasks, fallback_prompt, trace, source):
    data = parse_json(text)
    tasks = (data or {}).get("subtasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list) or not tasks:
        trace.append({"role": source, "note": "no parseable subtask JSON — "
                                          "falling back to a single subtask"})
        return [{"title": "original task", "prompt": fallback_prompt}]
    clean = []
    for item in tasks:
        if isinstance(item, dict) and str(item.get("prompt", "")).strip():
            clean.append({"title": str(item.get("title", "subtask"))[:80],
                          "prompt": str(item["prompt"]).strip()})
    clean = clean[:max_subtasks]
    if not clean:
        trace.append({"role": source, "note": "subtasks had no prompts — "
                                          "falling back to a single subtask"})
        return [{"title": "original task", "prompt": fallback_prompt}]
    return clean


def run_subtask(index, subtask, timeout_s, trace):
    """One subagent: basal picks the model for this subtask (RPM-aware) and executes."""
    routing = f"{ROLE_TASKS['executor']} Subtask: {subtask['title']}. {subtask['prompt'][:400]}"
    models, rpm_note = available_models()
    if not models:
        return {"title": subtask["title"], "error": router.NO_MODELS_HINT}
    decision = router.route(routing, models)
    step = {"role": "executor", "subtask": subtask["title"], "picked": decision["model"],
            "model_id": decision["model_id"], "chosen_by_basal": decision["chosen_by_basal"],
            "confidence": decision["confidence"], "deferred": decision["deferred"]}
    if rpm_note:
        step["rpm_note"] = rpm_note
    trace.append(step)
    try:
        text, errors = _execute_with_fallback(decision, subtask["prompt"], timeout_s)
        if errors:
            step["fallback_errors"] = errors
        return {"title": subtask["title"], "model": decision["model"],
                "model_id": step["model_id"], "ok": True, "output": text}
    except Exception as exc:
        step["failed"] = str(exc)[:200]
        return {"title": subtask["title"], "model": decision["model"],
                "model_id": step["model_id"], "ok": False, "error": str(exc)[:400]}


def run_subtasks(subtasks, parallel, timeout_s, trace):
    trace.append({"role": "execution", "note": f"{len(subtasks)} subtasks, "
                                               f"{parallel} parallel workers"})
    results = [None] * len(subtasks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {pool.submit(run_subtask, i, sub, timeout_s, trace): i
                   for i, sub in enumerate(subtasks)}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()
    return results


def synthesize(task, results, trace, timeout_s):
    ok_results = [r for r in results if r.get("ok")]
    if not ok_results:
        raise RuntimeError("; ".join(str(r.get("error")) for r in results) or "no subtask results")
    compact = [{"title": r["title"], "model": r["model_id"],
                "result": (r.get("output") or "")[:RESULT_CHARS]} for r in ok_results]
    prompt = ("You are the swarm synthesizer. Using the subagent results below, write the final "
              "complete answer to the original task. Keep facts from the subagent results; "
              "do not invent new facts.\n\n"
              f"Original task:\n{task}\n\nSubagent results:\n{json.dumps(compact, ensure_ascii=False)}")
    return call_role("aggregator", prompt, trace, timeout_s)


def plan_loop(task, trace, timeout_s):
    """ultra only: plan -> critique -> (one revision when fixes are required)."""
    plan = call_role("planner", f"You are the planner of a local model swarm. Produce a concise, "
                                f"numbered action plan for the task below. Each step must be "
                                f"executable by a single LLM subagent with no shared memory. "
                                f"Output the plan as plain text.\n\nTask:\n{task}", trace, timeout_s)
    for round_no in (1, 2):
        verdict = call_role("critic", "You review action plans before execution. Is this plan "
                                      "correct, complete and executable as stated? Output STRICT "
                                      'JSON only: {"ok": true|false, "fixes": "one sentence of '
                                      'required fixes, or empty"}\n\n'
                                      f"Task the plan must accomplish:\n{task}\n\nPlan:\n{plan}",
                            trace, timeout_s, note=f"review round {round_no}")
        data = parse_json(verdict) or {}
        trace.append({"role": "critic", "note": f"round {round_no}: ok={data.get('ok')} "
                                          f"fixes={str(data.get('fixes', ''))[:120]}"})
        if data.get("ok", True):
            return plan
        fixes = str(data.get("fixes", "")).strip()
        if not fixes:
            return plan
        plan = call_role("planner", "Revise this plan to address the reviewer's fixes. Output the "
                                   f"full revised plan as plain text.\n\nTask:\n{task}\n\n"
                                   f"Current plan:\n{plan}\n\nReviewer's fixes:\n{fixes}",
                        trace, timeout_s, note="revision after review")
    return plan


def run_swarm(task, mode="swarm", parallel=None, max_subtasks=None):
    if mode not in ("swarm", "ultra"):
        raise ValueError("mode must be 'swarm' or 'ultra'")
    settings = router.load_settings()
    parallel = int(parallel or settings.get("swarm_parallel", 2) or 2)
    max_subtasks = int(max_subtasks or settings.get("swarm_max_subtasks", 5) or 5)
    timeout_s = int(settings.get("exec_timeout_s", 600) or 600)
    trace = []
    started = time.time()

    if mode == "ultra":
        plan = plan_loop(task, trace, timeout_s)
        manager_prompt = ("You are the swarm manager. Turn the approved plan below into at most "
                          f"{max_subtasks} subagent task assignments. Each assignment prompt must "
                          "be fully self-contained (all context included, no references to other "
                          'subagents). Output STRICT JSON only: {"subtasks": [{"title": "short", '
                          '"prompt": "full self-contained prompt for the subagent"}]}\n\n'
                          f"Task:\n{task}\n\nApproved plan:\n{plan}")
        raw = call_role("manager", manager_prompt, trace, timeout_s)
        subtasks = parse_subtasks(raw, max_subtasks, task, trace, "manager")
    else:
        decomposer_prompt = ("You are the task decomposer of a local model swarm. Split the task "
                             f"below into at most {max_subtasks} INDEPENDENT subagent tasks. Each "
                             "subtask prompt must be fully self-contained (include all needed "
                             'context; never reference other subtasks). Output STRICT JSON only: '
                             '{"subtasks": [{"title": "short", "prompt": "full self-contained '
                             'prompt for the subagent"}]}\n\n'
                             f"Task:\n{task}")
        raw = call_role("decomposer", decomposer_prompt, trace, timeout_s)
        subtasks = parse_subtasks(raw, max_subtasks, task, trace, "decomposer")

    results = run_subtasks(subtasks, parallel, timeout_s, trace)
    final = synthesize(task, results, trace, timeout_s)
    return {"mode": mode, "task": task[:400], "subtasks": len(subtasks),
            "steps": trace, "results": [{k: v for k, v in r.items() if k != "output"}
                                        for r in results],
            "final": final, "elapsed_s": round(time.time() - started, 1)}


def build_footer(outcome):
    picks = {}
    for step in outcome["steps"]:
        if "picked" in step:
            picks.setdefault(step["role"], []).append(step.get("model_id", step["picked"]))
    parts = []
    if "planner" in picks:
        parts.append(f"plan by {', '.join(picks['planner'])}")
    if "critic" in picks:
        parts.append(f"critique by {', '.join(picks['critic'])}")
    if "manager" in picks:
        parts.append(f"subtasks written by {', '.join(picks['manager'])}")
    if "decomposer" in picks:
        parts.append(f"subtasks written by {', '.join(picks['decomposer'])}")
    if "executor" in picks:
        parts.append(f"{len(picks['executor'])} subagents ({', '.join(picks['executor'])})")
    if "aggregator" in picks:
        parts.append(f"synthesized by {', '.join(picks['aggregator'])}")
    return (f"---\n[router] swarm ({outcome['mode']}): " + "; ".join(parts) +
            f". {outcome['subtasks']} subtasks in {outcome['elapsed_s']}s, every step delegated "
            "by basal under RPM limits.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--mode", choices=["swarm", "ultra"], default="swarm")
    parser.add_argument("--parallel", type=int)
    parser.add_argument("--max-subtasks", type=int)
    parser.add_argument("--footer", action="store_true", help="append the router footer")
    args = parser.parse_args()
    outcome = run_swarm(args.task, args.mode, args.parallel, args.max_subtasks)
    text = outcome["final"]
    if args.footer:
        text += "\n\n" + build_footer(outcome)
    print(json.dumps({**outcome, "final": text}, ensure_ascii=False, indent=1)
          if args.footer else text)


if __name__ == "__main__":
    main()
