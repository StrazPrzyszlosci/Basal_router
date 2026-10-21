#!/home/krzysiek/basal-env/bin/python
"""Router configuration research: top-1 accuracy, confidence, latency, automation thresholds.

Configs (pick with the first argument, e.g. `run_experiments.py C1-manual-cards,C5-english-cards`):
  C1-manual-cards    — hand-written cards v1 (models.manual.json), baseline
  C2-net-cards       — raw models.dev cards (models.net.json)
  C3-hybrid-cards    — role + appended facts (models.hybrid.json)
  C4-manual-v2-cards — manual-v2 cards, Polish (models.manual-v2.json)
  C5-english-cards   — manual-v2 cards, English (models.json, production)

`run_experiments.py report` regenerates RESULTS.md from all eval_*.jsonl files (no latency columns —
latency is machine-load dependent; raw numbers live in experiments/results/last_run.json).
Benchmark task texts are intentionally Polish: they are the router's real-world input language.
"""
import json
import socket
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

DIR = Path(__file__).resolve().parent
ROOT = DIR.parent
sys.path.insert(0, str(ROOT))
import router  # noqa: E402

BASAL = router.BASAL_URL
OUT = DIR / "results"
OUT.mkdir(exist_ok=True)
TASKS = [json.loads(line) for line in (DIR / "benchmark_tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
FLOORS = (0.3, 0.45, 0.55, 0.7)
CONFIGS = {
    "C1-manual-cards": {"registry": "models.manual.json", "label": "hand-written cards (v1)"},
    "C2-net-cards": {"registry": "models.net.json", "label": "raw models.dev cards"},
    "C3-hybrid-cards": {"registry": "models.hybrid.json", "label": "hybrid (role + facts)"},
    "C4-manual-v2-cards": {"registry": "models.manual-v2.json", "label": "manual-v2 cards (Polish)"},
    "C5-english-cards": {"registry": "models.json", "label": "manual-v2 cards (English, production)"},
    "C6-benchmark-cards": {"registry": "models.json", "label": "cards with real benchmark scores"},
}


def criteria_from(registry_file):
    data = json.loads((ROOT / registry_file).read_text(encoding="utf-8"))
    return {m["key"]: f"{m['name']}. {m['card']}" for m in data["models"]}


def run_config(name, registry_file):
    criteria = criteria_from(registry_file)
    rows = []
    progress_path = OUT / f"progress_{name}.json"
    if progress_path.exists():
        try:
            rows = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:
            rows = []
    done = {r["id"] for r in rows if "error" not in r}
    for task in TASKS:
        if task["id"] in done:
            print(f"[{name}] {task['id']}: skipped (resumed from progress file)", flush=True)
            continue
        request = {"state": task["task"], "lang": "pl",
                   "questions": {"model": {"type": "choice", "instructions": router.INSTRUCTIONS,
                                           "criteria": criteria}}}
        t0 = time.time()
        try:
            response = httpx.post(f"{BASAL}/v1/systemone", json=request, timeout=300)
            response.raise_for_status()
            body = response.json()
            answer = body["answers"]["model"]
            row = {"id": task["id"], "profile": task["profile"], "gold": task["gold"],
                   "chosen": answer["choice"], "confidence": answer["confidence"],
                   "top3": [[k, round(v, 4)] for k, v in
                            sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])[:3]],
                   "latency_ms": body.get("usage", {}).get("latency_ms"),
                   "wall_s": round(time.time() - t0, 1)}
        except Exception as exc:
            row = {"id": task["id"], "profile": task["profile"], "gold": task["gold"],
                   "error": str(exc), "wall_s": round(time.time() - t0, 1)}
        rows.append(row)
        progress_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        if "error" in row:
            print(f"[{name}] {row['id']}: ERROR {row['error']}", flush=True)
        else:
            print(f"[{name}] {row['id']}: chosen={row['chosen']} conf={row['confidence']:.2f} "
                  f"gold={row['gold']} {row['wall_s']}s", flush=True)
    with (OUT / f"eval_{name}.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            entry = {"state": next(t["task"] for t in TASKS if t["id"] == row["id"]),
                     "lang": "pl",
                     "questions": {"model": {"type": "choice", "instructions": router.INSTRUCTIONS,
                                             "criteria": criteria, "gold": row["gold"]}},
                     "chosen": row.get("chosen"), "confidence": row.get("confidence"), "config": name}
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return rows


def summarize(rows):
    ok = [r for r in rows if "error" not in r]
    if not ok:
        return {"top1_accuracy": None, "note": "no successful measurements"}
    correct = [r for r in ok if r["chosen"] == r["gold"]]
    wrong = [r for r in ok if r["chosen"] != r["gold"]]
    lat = sorted(r.get("latency_ms") or 0 for r in ok)
    floors = {}
    for floor in FLOORS:
        auto = [r for r in ok if r["confidence"] >= floor]
        errs = [r for r in auto if r["chosen"] != r["gold"]]
        floors[str(floor)] = {"automated": len(auto),
                              "automation_rate": round(len(auto) / len(ok), 3),
                              "errors_among_automated": len(errs),
                              "error_rate_among_automated": round(len(errs) / len(auto), 3) if auto else None}
    return {
        "n": len(rows), "request_errors": len(rows) - len(ok),
        "top1_accuracy": round(len(correct) / len(ok), 3),
        "mean_conf_correct": round(statistics.mean(r["confidence"] for r in correct), 3) if correct else None,
        "mean_conf_wrong": round(statistics.mean(r["confidence"] for r in wrong), 3) if wrong else None,
        "median_latency_ms": round(statistics.median(lat)) if any(lat) else None,
        "p90_latency_ms": round(lat[int(0.9 * (len(lat) - 1))]) if any(lat) else None,
        "floors": floors,
    }


def rows_from_eval():
    bench = {t["task"]: t for t in TASKS}
    configs, excluded = {}, []
    for path in sorted(OUT.glob("eval_*.jsonl")):
        name = path.stem.replace("eval_", "")
        rows, stale = [], False
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            task = bench.get(e["state"])
            question = e["questions"]["model"]
            if task and question.get("gold") != task["gold"]:
                stale = True
                break
            rows.append({"id": task["id"] if task else "?",
                         "profile": task["profile"] if task else "?",
                         "gold": question.get("gold"),
                         "chosen": e.get("chosen"),
                         "confidence": e.get("confidence") or 0})
        if stale:
            excluded.append(name)
            continue
        if rows:
            configs[name] = rows
    return configs, excluded


def header_lines(results, with_latency):
    lines = ["# Router configuration research results", "",
             f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')} · {socket.gethostname()} "
             "(Intel i3-10110U, 4 threads, RAM under load) · basal-2.0-mini 1.5B Q8_0 via Ollama · "
             f"benchmark v2: {len(TASKS)} tasks, gold labels follow real models.dev profiles", "",
             "Configs: " + ", ".join(f"**{n}** ({CONFIGS.get(n, {}).get('label', n)})"
                                     for n in results) + ".", ""]
    if not with_latency:
        lines += ["Report regenerated from all `eval_*.jsonl` measurement sets; latency columns omitted "
                  "(machine-load dependent — raw numbers in `experiments/results/last_run.json`).", ""]
    lines += ["## Summary", ""]
    if with_latency:
        lines += ["| config | n | top-1 | mean conf (correct) | mean conf (wrong) | median [ms] | p90 [ms] |",
                  "|---|---|---|---|---|---|---|"]
    else:
        lines += ["| config | n | top-1 | mean conf (correct) | mean conf (wrong) |",
                  "|---|---|---|---|---|"]
    for name, s in results.items():
        label = CONFIGS.get(name, {}).get("label", name)
        cells = (f"| {name} ({label}) | {s['n']} | {s['top1_accuracy']} | "
                 f"{s['mean_conf_correct']} | {s['mean_conf_wrong']} |")
        if with_latency:
            cells += f" {s['median_latency_ms']} | {s['p90_latency_ms']} |"
        lines.append(cells)
    lines += ["", "## Automation thresholds (a decision is automated when confidence >= threshold)", "",
              "| config | threshold | automated % | errors among automated |", "|---|---|---|---|"]
    for name, s in results.items():
        for floor, d in s.get("floors", {}).items():
            lines.append(f"| {name} | {floor} | {d['automation_rate'] * 100:.0f}% | "
                         f"{d['errors_among_automated']} (rate {d['error_rate_among_automated']}) |")
    lines += ["", "## Per task", ""]
    names = list(results)
    header = "| id | profile | gold | " + " | ".join(names) + " |"
    lines += [header, "|---|---|---|" + "|---|" * len(names)]
    return lines


def per_task_lines(results, rows_by_config):
    names = list(results)
    by_config = {n: {r["id"]: r for r in rows_by_config.get(n, [])} for n in names}
    lines = []
    for task in TASKS:
        cells = []
        for n in names:
            row = by_config.get(n, {}).get(task["id"])
            if not row or not row.get("chosen"):
                cells.append("ERR")
                continue
            mark = "OK" if row["chosen"] == task["gold"] else "MISS"
            conf = row.get("confidence") or 0
            cells.append(f"{mark} {row['chosen']} ({conf:.2f})")
        lines.append(f"| {task['id']} | {task['profile']} | {task['gold']} | " + " | ".join(cells) + " |")
    lines += ["", "Benchmark task texts are intentionally Polish (the router's real input language). "
              "`eval_*.jsonl` files keep the exact prompts of each measurement and are ready input for "
              "`basal-calibrate` and GEPA.", ""]
    return lines


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        configs, excluded = rows_from_eval()
        results = {n: summarize(r) for n, r in configs.items()}
        text = header_lines(results, with_latency=False)
        if excluded:
            text.append(f"Note: {', '.join(excluded)} measured under older gold labels — excluded from "
                        "this report (kept in git history and in the eval files).")
            text.append("")
        text += per_task_lines(results, configs)
        (ROOT / "RESULTS.md").write_text("\n".join(text), encoding="utf-8")
        print("DONE: " + str(ROOT / "RESULTS.md"), flush=True)
        return
    if len(sys.argv) > 1 and sys.argv[1] != "all":
        selected = sys.argv[1].split(",")
    else:
        selected = list(CONFIGS)
    for name in selected:
        if name not in CONFIGS:
            raise SystemExit(f"unknown config: {name}; available: {', '.join(CONFIGS)}")
    rows_by_config, results = {}, {}
    for name in selected:
        rows_by_config[name] = run_config(name, CONFIGS[name]["registry"])
        results[name] = summarize(rows_by_config[name])
    payload = {"meta": {"ts": datetime.now().isoformat(), "host": socket.gethostname(),
                        "model": "basal-2.0-mini 1.5B Q8_0 (ollama)", "tasks": len(TASKS),
                        "configs": {n: CONFIGS[n]["registry"] for n in selected}},
               "results": results, "rows": rows_by_config}
    (OUT / "last_run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    text = header_lines(results, with_latency=True) + per_task_lines(results, rows_by_config)
    (ROOT / "RESULTS.md").write_text("\n".join(text), encoding="utf-8")
    print("DONE: " + str(ROOT / "RESULTS.md"), flush=True)


if __name__ == "__main__":
    main()
