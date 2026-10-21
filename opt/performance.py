#!/home/krzysiek/basal-env/bin/python
"""Passive performance measurement (ToS-respecting).

No synthetic calls are made to hosted free models — opencode's Terms of Service restrict automated
retrieval, so measurements come from real user requests only. Direct provider APIs report completion
tokens (real tok/s); the opencode CLI reports event timestamps but no token counts, so those models
get total time only and tok/s stays honestly unknown. Models without measurements show no speed claims.

Storage: opt/performance/metrics.jsonl — one line per call, timing data only, never task text.
Speed Score (0-100) follows the audit formula: T_200 = 200 / tok_s, score = round(100 / (1 + T_200/20))
— a utility indicator, not an industry benchmark.

Usage:
  ~/basal-env/bin/python opt/performance.py status     # per-model stats
  ~/basal-env/bin/python opt/performance.py refresh    # rewrite the Speed(...) line in every card
"""
import argparse
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "opt" / "performance"
METRICS = DIR / "metrics.jsonl"
MIN_SAMPLES = 2


def record(model_key, runner, ok, duration_s, completion_tokens=None):
    DIR.mkdir(parents=True, exist_ok=True)
    with METRICS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "model": model_key, "runner": runner, "ok": bool(ok),
            "duration_s": round(duration_s, 2),
            "completion_tokens": completion_tokens,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")


def stats():
    per = {}
    if not METRICS.exists():
        return {}
    for line in METRICS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        per.setdefault(row["model"], []).append(row)
    out = {}
    for key, rows in per.items():
        ok_rows = [r for r in rows if r.get("ok")]
        durations = sorted(r["duration_s"] for r in ok_rows)
        tps_values = [r["completion_tokens"] / r["duration_s"] for r in ok_rows
                      if r.get("completion_tokens") and r.get("duration_s", 0) > 0]
        median_tps = statistics.median(tps_values) if tps_values else None
        out[key] = {
            "n": len(rows),
            "ok_rate": round(len(ok_rows) / len(rows), 2) if rows else 0,
            "median_s": round(statistics.median(durations), 1) if durations else None,
            "p90_s": round(durations[int(0.9 * (len(durations) - 1))], 1) if len(durations) > 1 else None,
            "tok_s": round(median_tps) if median_tps else None,
            "speed_score": round(100 / (1 + (200 / median_tps) / 20)) if median_tps else None,
            "runner": rows[0].get("runner"),
            "updated": rows[-1].get("ts"),
        }
    return out


def speed_line(key):
    s = stats().get(key)
    if not s or s["n"] < MIN_SAMPLES:
        return ""
    parts = [f"median {s['median_s']} s/call over {s['n']} measured calls, ok {int(s['ok_rate'] * 100)}%"]
    if s["tok_s"]:
        parts.append(f"{s['tok_s']} tok/s")
        if s["speed_score"]:
            parts.append(f"speed score {s['speed_score']}/100")
    else:
        parts.append("tok/s not reported by this runner")
    return f"Speed (measured passively): {'; '.join(parts)}."


def refresh_lines():
    """Idempotently rewrite the Speed(...) suffix of every card from measured data."""
    import sys
    sys.path.insert(0, str(ROOT))
    import router
    registry = router.load_registry()
    updated = {}
    for m in registry["models"]:
        card = m["card"].split(" Speed (measured")[0].rstrip()
        line = speed_line(m["key"])
        if line:
            card = f"{card} {line}"
        m["card"] = card
        updated[m["key"]] = bool(line)
    router.REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    print(json.dumps({"cards_with_speed": sum(1 for v in updated.values() if v),
                      "of": len(updated)}, ensure_ascii=False))


def status():
    data = stats()
    if not data:
        print("no measurements yet — passive recording starts with the first real execution")
        return
    for key, s in data.items():
        print(f"  {key}: n={s['n']} ok={int(s['ok_rate'] * 100)}% median={s['median_s']}s "
              f"p90={s['p90_s']}s tok/s={s['tok_s']} score={s['speed_score']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="status", choices=["status", "refresh"])
    args = parser.parse_args()
    refresh_lines() if args.mode == "refresh" else status()


if __name__ == "__main__":
    main()
