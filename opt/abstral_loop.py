#!/home/krzysiek/basal-env/bin/python
"""ABSTRAL-style architecture learning for the router pipeline.

Adapted from the concept of "ABSTRAL: Automatic Design of Multi-Agent Systems Through Iterative
Refinement and Topology Optimization" (arXiv:2603.22791) — an adaptation, not an integration:
the paper optimises multi-agent architectures; here the "organisation" being learned is the
router pipeline (route -> run -> verify -> escalate) and its topology knobs.

One cycle = BUILD -> RUN -> ANALYZE -> UPDATE, with the guardrails this kind of loop requires:
- cost register: every cycle records model calls (executions + escalations)
- regression gate: a topology change applied last cycle that lowered accuracy is auto-reverted
- full revert: every automatic change is journaled in change_log.json with its previous values

Usage:
  ~/basal-env/bin/python opt/abstral_loop.py --tasks 3      # one cycle (3 tasks ~ 12-25 min)
  ~/basal-env/bin/python opt/abstral_loop.py --status
  ~/basal-env/bin/python opt/abstral_loop.py --revert <cycle>
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

DIR = ROOT / "opt" / "abstral"
ARCH_PATH = DIR / "architecture.json"
CHANGELOG_PATH = DIR / "change_log.json"
ROUTERD = "http://127.0.0.1:8788/v1/chat/completions"
ACCURACY_TOLERANCE = 0.05

ESCALATION_MODELS = {"nemotron-ultra", "beam", "big-pickle", "nemotron-lightning"}
TOPOLOGY_BOUNDS = {"verify_threshold": (0.4, 0.9), "max_attempts": (1, 3),
                   "confidence_floor": (0.3, 0.9)}

DEFAULT_ARCH = {
    "version": 1,
    "topology": {"verify": True, "verify_threshold": 0.6,
                 "escalation_model": "nemotron-ultra", "max_attempts": 2,
                 "fallback_enabled": True, "confidence_floor": None},
    "knowledge": {"K": [], "R": [], "T": [], "P": []},
    "runs": [],
}


def load_arch():
    if ARCH_PATH.exists():
        return json.loads(ARCH_PATH.read_text(encoding="utf-8"))
    DIR.mkdir(parents=True, exist_ok=True)
    save_arch(DEFAULT_ARCH)
    return json.loads(json.dumps(DEFAULT_ARCH))


def save_arch(arch):
    DIR.mkdir(parents=True, exist_ok=True)
    ARCH_PATH.write_text(json.dumps(arch, ensure_ascii=False, indent=2), encoding="utf-8")


def load_changelog():
    if CHANGELOG_PATH.exists():
        return json.loads(CHANGELOG_PATH.read_text(encoding="utf-8"))
    return []


def save_changelog(log):
    CHANGELOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(task, topo):
    """route -> run -> verify (-> escalate) under the current topology; returns a trace."""
    trace = {"model_calls": 1, "escalated": False}
    decision = router.route(task["task"])
    answer = router.execute(decision["model"], task["task"])
    trace.update({"chosen": decision["chosen_by_basal"], "confidence": round(decision["confidence"], 3),
                  "deferred": decision["deferred"]})
    verified = None
    if topo.get("verify", True):
        request = {"state": f"Task:\n{task['task']}\n\nAnswer:\n{answer[-3000:]}", "lang": "en",
                   "questions": {"ok": {"type": "noul",
                                        "instructions": "Does the answer above properly address the task?",
                                        "criteria": {"true": "yes", "false": "no"}}}}
        response = httpx.post(f"{router.BASAL_URL}/v1/systemone", json=request, timeout=180)
        response.raise_for_status()
        p_ok = response.json()["answers"]["ok"]["noul"]
        verified = p_ok >= topo.get("verify_threshold", 0.6)
        trace["verify_p"] = round(p_ok, 3)
    if verified is False and topo.get("max_attempts", 2) > 1:
        answer = router.execute(topo.get("escalation_model", "nemotron-ultra"), task["task"])
        trace["model_calls"] += 1
        trace["escalated"] = True
        verified = True
    trace["verified"] = verified
    return trace


def analyze(traces):
    n = len(traces)
    return {
        "n": n,
        "routing_accuracy": round(sum(1 for t in traces if t["chosen"] == t["gold"]) / n, 3),
        "verified_rate": round(sum(1 for t in traces if t.get("verified")) / n, 3),
        "escalation_rate": round(sum(1 for t in traces if t.get("escalated")) / n, 3),
        "mean_model_calls": round(sum(t["model_calls"] for t in traces) / n, 2),
        "deferred_rate": round(sum(1 for t in traces if t.get("deferred")) / n, 3),
    }


def reflect(arch, stats, traces):
    prompt = (
        "You are the UPDATE step of an ABSTRAL-style loop optimising the architecture of a local "
        "model-router pipeline (route -> run -> verify -> escalate).\n"
        f"Current topology: {json.dumps(arch['topology'])}\n"
        f"Knowledge K/R/T/P: {json.dumps(arch['knowledge'], ensure_ascii=False)[:2000]}\n"
        f"Last run stats: {json.dumps(stats)}\n"
        f"Per-task traces: {json.dumps(traces, ensure_ascii=False)[:3000]}\n"
        f"Valid escalation models: {', '.join(sorted(ESCALATION_MODELS))}.\n"
        "Propose the smallest change that improves routing accuracy or verification economics. "
        'Output STRICT JSON only: {"topology": {only the changed fields}, '
        '"K": [at most 2 new domain facts], "R": [at most 2 routing rules], '
        '"T": [at most 2 role templates], "P": [at most 2 protocol notes], '
        '"reason": "one sentence"}. No markdown, no prose outside JSON.'
    )
    response = httpx.post(ROUTERD, json={"model": "auto",
                                         "messages": [{"role": "user", "content": prompt}]},
                          timeout=600)
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"].strip()
    marker = "\n---\n[router]"
    if marker in text:
        text = text.split(marker)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def apply_update(arch, proposal, cycle_no, stats):
    changelog = load_changelog()
    entry = {"cycle": cycle_no, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "changes": {}, "reason": "", "validated": None, "reverted": False}
    topo = arch["topology"]
    for key, value in (proposal.get("topology") or {}).items():
        if key in TOPOLOGY_BOUNDS:
            low, high = TOPOLOGY_BOUNDS[key]
            if isinstance(value, (int, float)) and low <= value <= high:
                entry["changes"][key] = {"old": topo.get(key), "new": value}
                topo[key] = value
        elif key in ("verify", "fallback_enabled") and isinstance(value, bool):
            entry["changes"][key] = {"old": topo.get(key), "new": value}
            topo[key] = value
        elif key == "escalation_model" and value in ESCALATION_MODELS:
            entry["changes"][key] = {"old": topo.get(key), "new": value}
            topo[key] = value
    for area in ("K", "R", "T", "P"):
        for item in (proposal.get(area) or [])[:2]:
            text = str(item)[:300]
            if text and text not in arch["knowledge"][area]:
                arch["knowledge"][area].append(text)
    entry["reason"] = str(proposal.get("reason", ""))[:300]
    changelog.append(entry)
    save_changelog(changelog)
    return entry


def revert_cycle(cycle_no):
    arch = load_arch()
    changelog = load_changelog()
    entry = next((e for e in changelog if e["cycle"] == cycle_no), None)
    if not entry:
        raise SystemExit(f"no changelog entry for cycle {cycle_no}")
    for key, change in entry["changes"].items():
        arch["topology"][key] = change["old"]
    entry["reverted"] = True
    save_arch(arch)
    save_changelog(changelog)
    print(f"reverted cycle {cycle_no}: {list(entry['changes'])}")


def regression_gate(arch, changelog, stats):
    """If last cycle's applied changes regressed accuracy, auto-revert them."""
    previous = next((e for e in reversed(changelog) if e["changes"] and not e["reverted"]), None)
    if not previous:
        return
    prev_run = next((r for r in reversed(arch["runs"]) if r["cycle"] == previous["cycle"] - 1), None)
    if prev_run and stats["routing_accuracy"] < prev_run["accuracy"] - ACCURACY_TOLERANCE:
        for key, change in previous["changes"].items():
            arch["topology"][key] = change["old"]
        previous["reverted"] = True
        previous["validated"] = False
        print(f"[gate] accuracy regressed ({prev_run['accuracy']} -> {stats['routing_accuracy']}); "
              f"auto-reverted cycle {previous['cycle']}: {list(previous['changes'])}", flush=True)
    elif prev_run:
        previous["validated"] = True


def cycle(n_tasks):
    arch = load_arch()
    changelog = load_changelog()
    cycle_no = len(arch["runs"]) + 1
    bench = [json.loads(line) for line in
             (ROOT / "experiments" / "benchmark_tasks.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()][:n_tasks]
    traces = []
    for task in bench:
        try:
            trace = run_pipeline(task, arch["topology"])
        except Exception as exc:
            trace = {"chosen": f"error: {str(exc)[:120]}", "confidence": 0, "deferred": False,
                     "verified": None, "model_calls": 0, "escalated": False}
        trace["gold"] = task["gold"]
        trace["profile"] = task["profile"]
        traces.append(trace)
        print(f"[cycle {cycle_no}] {task['id']}: chosen={trace['chosen']} gold={task['gold']} "
              f"verified={trace.get('verified')} calls={trace['model_calls']}", flush=True)
    stats = analyze(traces)
    print(f"[cycle {cycle_no}] stats: {json.dumps(stats)}", flush=True)
    regression_gate(arch, changelog, stats)
    arch["runs"].append({"cycle": cycle_no, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "accuracy": stats["routing_accuracy"], "stats": stats})
    save_arch(arch)
    save_changelog(changelog)
    proposal = reflect(arch, stats, traces)
    if proposal is None:
        print("[update] reflection produced no parseable proposal; keeping architecture", flush=True)
        return
    entry = apply_update(arch, proposal, cycle_no, stats)
    save_arch(arch)
    print(f"[update] applied {list(entry['changes']) or 'knowledge-only'} :: {entry['reason']}", flush=True)
    print("DONE cycle " + str(cycle_no), flush=True)


def status():
    arch = load_arch()
    changelog = load_changelog()
    print(json.dumps({"topology": arch["topology"],
                      "knowledge_items": {k: len(v) for k, v in arch["knowledge"].items()},
                      "runs": [{"cycle": r["cycle"], "accuracy": r["accuracy"],
                                "stats": r["stats"]} for r in arch["runs"][-5:]],
                      "changes": len(changelog),
                      "reverted": sum(1 for e in changelog if e["reverted"])},
                     ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=3)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--revert", type=int)
    args = parser.parse_args()
    if args.status:
        status()
        return
    if args.revert is not None:
        revert_cycle(args.revert)
        return
    cycle(args.tasks)


if __name__ == "__main__":
    main()
