#!/home/krzysiek/basal-env/bin/python
"""Badania konfiguracji routera: dokładność top-1, pewność, opóźnienia, progi automatyzacji.

Konfiguracje (wybór: pierwszy argument, np. `run_experiments.py C1-manual-cards,C3-hybrid-cards`):
  C1-manual-cards — karty ręczne (models.manual.json), baseline
  C2-net-cards    — karty wyłącznie z models.dev (models.net.json)
  C3-hybrid-cards — karty hybrydowe: rola wg realnych profili + fakty z models.dev (models.json)
Goldy benchmarku (v2) oparte na realnych profilach z models.dev.
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
    "C1-manual-cards": {"registry": "models.manual.json", "label": "karty ręczne (v1)"},
    "C2-net-cards": {"registry": "models.net.json", "label": "karty z models.dev"},
    "C3-hybrid-cards": {"registry": "models.json", "label": "karty hybrydowe (rola + fakty)"},
}


def criteria_from(registry_file):
    data = json.loads((ROOT / registry_file).read_text(encoding="utf-8"))
    return {m["key"]: f"{m['name']}. {m['card']}" for m in data["models"]}


def run_config(name, registry_file):
    criteria = criteria_from(registry_file)
    rows = []
    for task in TASKS:
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
        (OUT / f"progress_{name}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
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
        return {"top1_accuracy": None, "note": "brak udanych pomiarów"}
    correct = [r for r in ok if r["chosen"] == r["gold"]]
    wrong = [r for r in ok if r["chosen"] != r["gold"]]
    lat = sorted(r["latency_ms"] or 0 for r in ok)
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
        "median_latency_ms": round(statistics.median(lat)) if lat else None,
        "p90_latency_ms": round(lat[int(0.9 * (len(lat) - 1))]) if lat else None,
        "floors": floors,
    }


def render_md(results, rows_by_config):
    names = list(results)
    lines = ["# Wyniki badań konfiguracji routera", "",
             f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M')} · {socket.gethostname()} "
             "(Intel i3-10110U, 4 wątki, RAM pod obciążeniem) · basal-2.0-mini 1.5B Q8_0 przez Ollamę · "
             f"benchmark v2: {len(TASKS)} zadań, 15 opcji, goldy wg realnych profili models.dev", ""]
    lines += ["Przebiegi: " + ", ".join(f"**{n}** ({CONFIGS[n]['label']})" for n in names) + ".", ""]
    lines += ["## Zbiorczo", "",
              "| konfiguracja | top-1 | śr. pewność trafnych | śr. pewność błędnych | mediana [ms] | p90 [ms] |",
              "|---|---|---|---|---|---|"]
    for name, s in results.items():
        lines.append(f"| {name} ({CONFIGS[name]['label']}) | {s['top1_accuracy']} | {s['mean_conf_correct']} | "
                     f"{s['mean_conf_wrong']} | {s['median_latency_ms']} | {s['p90_latency_ms']} |")
    lines += ["", "## Progi automatyzacji (decyzja pewna, gdy confidence ≥ próg)", "",
              "| konfiguracja | próg | % zautomatyzowanych | błędy wśród zautomatyzowanych |", "|---|---|---|---|"]
    for name, s in results.items():
        for floor, d in s.get("floors", {}).items():
            lines.append(f"| {name} | {floor} | {d['automation_rate'] * 100:.0f}% | "
                         f"{d['errors_among_automated']} (rate {d['error_rate_among_automated']}) |")
    lines += ["", "## Per zadanie", ""]
    header = "| id | profil | gold | " + " | ".join(f"{n}" for n in names) + " |"
    lines += [header, "|---|---|---|" + "|---|" * len(names)]
    by_config = {n: {r["id"]: r for r in rows_by_config.get(n, [])} for n in names}

    def fmt(row):
        if not row or "error" in row:
            return "ERR"
        mark = "✓" if row.get("chosen") == row.get("gold") else "✗"
        return f"{mark} {row.get('chosen')} ({row.get('confidence', 0):.2f})"

    for task in TASKS:
        cells = " | ".join(fmt(by_config[n].get(task["id"])) for n in names)
        lines.append(f"| {task['id']} | {task['profile']} | {task['gold']} | {cells} |")
    lines += ["", "Goldy v2 wg realnych profili models.dev (rewizja: t07 beam→nemotron-ultra, "
              "t10 big-pickle→glm-flash). Historyczny wynik C2-net-cards na benchmarku v1: 0/10 "
              "(poprzedni run, w historii gita).", ""]
    if len(names) == 1:
        lines.append("Uwaga: przebieg jednej konfiguracji — porównanie z pozostałymi w poprzednich RESULTS "
                     "(historia gita) lub dociągnij `run_experiments.py <lista-konfiguracji>`.", "")
    lines += ["Surowe pomiary: `experiments/results/last_run.json`; `eval_*.jsonl` to gotowy input dla "
              "`basal-calibrate` i pod zbiór ewaluacyjny GEPA.", ""]
    return "\n".join(lines)


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "all":
        selected = sys.argv[1].split(",")
    else:
        selected = list(CONFIGS)
    for name in selected:
        if name not in CONFIGS:
            raise SystemExit(f"nieznana konfiguracja: {name}; dostępne: {', '.join(CONFIGS)}")
    rows_by_config, results = {}, {}
    for name in selected:
        rows_by_config[name] = run_config(name, CONFIGS[name]["registry"])
        results[name] = summarize(rows_by_config[name])
    payload = {"meta": {"ts": datetime.now().isoformat(), "host": socket.gethostname(),
                        "model": "basal-2.0-mini 1.5B Q8_0 (ollama)", "tasks": len(TASKS),
                        "configs": {n: CONFIGS[n]["registry"] for n in selected}},
               "results": results, "rows": rows_by_config}
    (OUT / "last_run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "RESULTS.md").write_text(render_md(results, rows_by_config), encoding="utf-8")
    print("GOTOWE: " + str(ROOT / "RESULTS.md"), flush=True)


if __name__ == "__main__":
    main()
